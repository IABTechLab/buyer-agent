# Budget Pacing & Reallocation

!!! info "Coming Soon — Phase 2"
    Budget pacing and reallocation (buyer-9zz) is part of Phase 2: Campaign Intelligence. It depends on the [Campaign Brief to Deal Pipeline](campaign-pipeline.md) (buyer-u8l). This page describes the planned budget management capability.

The budget pacing engine monitors campaign spend against plan in real time, detects over-delivery and under-delivery, and reallocates budget across channels and sellers mid-flight. When pacing is off-target, the engine can issue deal adjustment requests to sellers to bring the campaign back on track.

## What Budget Pacing Does

After a campaign's deals are booked (either manually or via the [campaign pipeline](campaign-pipeline.md)), the pacing engine tracks delivery against the plan. Without pacing, a campaign can exhaust its budget early (over-delivery) or fail to spend its budget (under-delivery) — both waste advertiser money or miss reach goals.

The pacing engine addresses three questions continuously:

1. **Are we on pace?** — Compare actual spend and impressions against the planned delivery curve
2. **What is off?** — Identify which channels, sellers, or deals are over- or under-delivering
3. **What should we do?** — Reallocate budget from under-performing deals to over-performing ones, or request delivery adjustments from sellers

## Planned Capabilities

### Spend Monitoring

- Track actual impressions and spend per deal, per channel, and per seller
- Compare against the planned delivery curve (linear pacing, front-loaded, or back-loaded)
- Surface pacing alerts when delivery deviates beyond configurable thresholds

### Delivery Detection

- **Over-delivery** — A deal or channel is spending faster than planned, risking early budget exhaustion
- **Under-delivery** — A deal or channel is spending slower than planned, risking unspent budget at flight end
- **Stalled delivery** — A deal has stopped delivering entirely (zero impressions over a configurable window)

### Budget Reallocation

- Shift budget from under-delivering deals to over-delivering ones within the same channel
- Shift budget across channels when an entire channel is under-delivering
- Respect minimum and maximum allocation constraints per seller and per channel
- Log all reallocation decisions with rationale for auditability

### Seller Adjustment Requests

- Issue deal modification requests to sellers when pacing requires delivery changes
- Request increased delivery on under-delivering deals (if seller has available inventory)
- Request throttled delivery on over-delivering deals
- Track seller responses and adjust the pacing model accordingly

## Key Planned Functionality

- **Real-time pacing dashboard** — Current spend vs. plan across all active deals
- **Configurable pacing curves** — Linear, front-loaded, back-loaded, or custom delivery curves
- **Automatic reallocation** — Rules-based budget shifting when pacing deviates beyond thresholds
- **Manual override** — Campaign manager can approve or reject reallocation recommendations
- **Deal adjustment API** — Request delivery changes from sellers via the [Deals API](../api/deals.md)
- **Pacing history** — Full audit trail of pacing measurements and reallocation decisions

## Integration Points

Budget pacing builds on several existing and planned buyer agent capabilities:

- [Campaign Brief to Deal Pipeline](campaign-pipeline.md) — Defines the initial budget allocation that pacing monitors
- [Deals API](../api/deals.md) — Used for deal status checks and modification requests
- [Multi-Seller Orchestration](multi-seller-orchestration.md) — Portfolio-level optimization informs reallocation decisions
- [Sessions](sessions.md) — Persistent seller sessions for mid-flight deal adjustments

## Related

- [Campaign Brief to Deal Pipeline](campaign-pipeline.md) — Initial campaign setup (pacing monitors what the pipeline books)
- [Deals API](../api/deals.md) — Deal status and modification endpoints
- [Multi-Seller Orchestration](multi-seller-orchestration.md) — Cross-seller portfolio management
- [Seller Agent Docs](https://iabtechlab.github.io/seller-agent/) — Seller-side deal management
