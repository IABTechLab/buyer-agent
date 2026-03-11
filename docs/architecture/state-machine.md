# Order State Machine

!!! info "Coming Soon"
    The formal order state machine is planned for the buyer agent, based on the seller agent's proven implementation. This page describes the planned state transition enforcement for the deal lifecycle.

The order state machine enforces valid state transitions for deals as they move through their lifecycle — from initial quote through booking, activation, and completion. By formalizing state transitions, the buyer agent can guarantee that deals never enter invalid states and that every transition is auditable.

## Why a State Machine

Today, deal status transitions in the buyer agent are tracked via the [DealStore](deal-store.md) and updated based on seller responses. The status field is a string that can be set freely. A formal state machine adds:

- **Transition validation** — Only defined transitions are allowed (e.g., a `completed` deal cannot move back to `proposed`)
- **Guard conditions** — Transitions can require preconditions (e.g., a deal cannot move to `active` without confirmed creative assets)
- **Transition hooks** — Actions triggered automatically on state change (e.g., notify the campaign manager when a deal moves to `active`)
- **Audit trail** — Every transition is logged with timestamp, actor, and reason

## Planned State Transitions

The buyer-side deal lifecycle will follow these states:

```
quoted --> proposed --> active --> completed
  |          |           |
  v          v           v
expired    rejected   cancelled
```

| From | To | Trigger | Guard |
|------|----|---------|-------|
| `quoted` | `proposed` | Buyer books the deal | Quote not expired |
| `quoted` | `expired` | Quote TTL exceeded | — |
| `proposed` | `active` | Seller activates the deal | — |
| `proposed` | `rejected` | Seller rejects the booking | — |
| `active` | `completed` | Impressions delivered / flight ended | — |
| `active` | `cancelled` | Buyer or seller cancels | Cancellation policy allows |

## Seller State Machine Reference

The seller agent already implements a formal order lifecycle state machine. The buyer's state machine will complement the seller's, tracking the buyer-side view of the same deal lifecycle.

See the seller's state machine documentation for the authoritative server-side implementation: [Seller Order Lifecycle](https://iabtechlab.github.io/seller-agent/state-machines/order-lifecycle/)

## Key Planned Functionality

- **Declarative state definitions** — States and transitions defined in configuration, not scattered through code
- **Transition guards** — Precondition checks before allowing a state change
- **Transition hooks** — Automatic side effects on state change (notifications, logging, metric updates)
- **Immutable transition log** — Append-only record of all state changes with metadata
- **Sync with seller state** — Map seller-side state changes to buyer-side transitions when the seller reports status updates

## Related

- [Deal Store](deal-store.md) — Current deal persistence (state machine builds on this)
- [Deals API](../api/deals.md) — Deal lifecycle statuses
- [Event Bus](event-bus.md) — Planned event logging (state transitions emit events)
- [Seller Order Lifecycle](https://iabtechlab.github.io/seller-agent/state-machines/order-lifecycle/) — Seller-side state machine
