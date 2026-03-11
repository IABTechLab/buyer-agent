# Event Bus

!!! info "Coming Soon"
    The event bus is planned for the buyer agent, based on the seller agent's implementation. This page describes the planned immutable event logging system for observability and auditability.

The event bus provides an immutable, append-only event log for all significant actions within the buyer agent. Every deal quote, negotiation round, booking, state transition, and budget reallocation is recorded as an event — giving operators full observability into what the buyer agent did, when, and why.

## Why an Event Bus

The buyer agent currently logs actions through standard Python logging and persists deal state in the [DealStore](deal-store.md). An event bus adds structured, queryable event records that serve multiple purposes:

- **Auditability** — Regulators, advertisers, and agency partners can trace every action the buyer agent took on their behalf
- **Debugging** — Reconstruct the exact sequence of events that led to a deal outcome
- **Analytics** — Query historical events for reporting (e.g., average negotiation rounds, win rates by seller, time-to-book)
- **Integration** — External systems can subscribe to events for real-time dashboards, alerting, or downstream processing

## Planned Event Types

| Event Type | Emitted When | Example Payload |
|------------|-------------|-----------------|
| `quote.requested` | Quote request sent to seller | `{seller_url, product_id, target_cpm}` |
| `quote.received` | Quote response received | `{quote_id, final_cpm, expires_at}` |
| `negotiation.started` | Negotiation session opened | `{proposal_id, strategy, opening_offer}` |
| `negotiation.round` | Each negotiation round completes | `{round_number, buyer_price, seller_price, action}` |
| `negotiation.completed` | Negotiation session ended | `{outcome, final_price, rounds_count}` |
| `deal.booked` | Deal booking confirmed | `{deal_id, quote_id, final_cpm}` |
| `deal.state_changed` | Deal status transition | `{deal_id, from_state, to_state, reason}` |
| `budget.reallocation` | Budget shifted between deals/channels | `{from_deal, to_deal, amount, reason}` |
| `creative.validated` | Creative spec validation completed | `{creative_id, format, result, violations}` |

## Seller Event Bus Reference

The seller agent already implements an event bus for server-side observability. The buyer's event bus will follow the same architectural pattern — immutable append-only log with structured events — applied to buyer-side actions.

See the seller's event bus documentation: [Seller Event Bus](https://iabtechlab.github.io/seller-agent/event-bus/overview/)

## Key Planned Functionality

- **Immutable event log** — Append-only storage; events are never modified or deleted
- **Structured events** — Each event has a type, timestamp, actor, and typed payload
- **Queryable history** — Filter and search events by type, time range, deal ID, seller, or actor
- **Event subscriptions** — Register handlers that fire when specific event types are emitted
- **Correlation IDs** — Trace a chain of related events across a multi-step workflow (e.g., from quote request through negotiation to booking)
- **Retention policies** — Configurable retention periods for compliance and storage management

## Related

- [Deal Store](deal-store.md) — Current deal persistence (event bus complements, does not replace)
- [Order State Machine](state-machine.md) — State transitions emit events to the event bus
- [Architecture Overview](overview.md) — System architecture context
- [Seller Event Bus](https://iabtechlab.github.io/seller-agent/event-bus/overview/) — Seller-side event bus implementation
