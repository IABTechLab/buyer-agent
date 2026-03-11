# Multi-Seller Deal Orchestration

!!! info "Coming Soon — Phase 2"
    Multi-seller deal orchestration (buyer-8ih) is part of Phase 2: Campaign Intelligence. This page describes the planned orchestrated deal execution capability. For the existing multi-seller *discovery and shopping* workflow, see [Multi-Seller Discovery](multi-seller.md).

Multi-seller deal orchestration extends the buyer agent from shopping multiple sellers individually to **coordinating deals across sellers as a unified portfolio**. Where the existing [multi-seller discovery](multi-seller.md) guide covers finding and comparing inventory, this capability adds strategic deal execution: running parallel negotiations, applying cross-seller optimization, and managing a portfolio of deals as a coordinated whole.

## What Orchestration Adds

The current multi-seller workflow lets you discover sellers, browse media kits, compare pricing, and negotiate with individual sellers. Each deal is independent — you decide which sellers to engage and manage each negotiation separately.

Orchestrated deal execution adds a coordination layer:

- **Parallel quote requests** — Request quotes from multiple sellers simultaneously, with configurable concurrency limits
- **Cross-seller negotiation strategy** — Negotiate with awareness of competing offers (e.g., use Seller A's quote as leverage with Seller B)
- **Portfolio Manager orchestration** — The Portfolio Manager agent (Opus-level) evaluates the full set of available deals and selects the combination that best satisfies campaign objectives
- **Atomic portfolio booking** — Book a set of deals as a coordinated action, with rollback handling if individual bookings fail
- **Seller ranking and allocation** — Allocate budget across sellers based on historical performance, pricing, and inventory quality

## Portfolio Manager

The Portfolio Manager is the orchestration agent responsible for cross-seller strategy. It sits above the channel specialist agents in the [agent hierarchy](../architecture/overview.md) and makes portfolio-level decisions that no individual channel specialist can make alone.

### Planned Responsibilities

- **Budget allocation** — Distribute campaign budget across sellers and channels based on objectives and constraints
- **Deal scoring** — Score each candidate deal on price efficiency, reach, audience match, and seller reliability
- **Portfolio selection** — Select the optimal set of deals that maximizes campaign KPIs within budget
- **Competitive leverage** — Inform negotiation strategies with cross-seller intelligence (e.g., competing quotes)
- **Risk management** — Diversify across sellers to mitigate delivery risk

## Orchestration Flow

The planned orchestration flow extends the existing multi-seller workflow:

1. **Discover and filter sellers** — Using the existing [registry client](../api/seller-discovery.md) and [media kit browsing](media-kit.md)
2. **Request quotes in parallel** — Submit quote requests to all qualifying sellers concurrently via the [Deals API](../api/deals.md)
3. **Evaluate the quote landscape** — Portfolio Manager reviews all returned quotes as a set
4. **Run coordinated negotiations** — Negotiate with multiple sellers simultaneously, with cross-seller awareness
5. **Select the optimal portfolio** — Choose the combination of deals that best satisfies the campaign brief
6. **Book the portfolio** — Execute bookings across sellers, handling partial failures gracefully

## Key Planned Functionality

- **Concurrent seller engagement** — Engage N sellers in parallel with configurable concurrency (default: 5 concurrent)
- **Cross-seller intelligence** — Negotiation strategies informed by the full competitive landscape, not just the current seller
- **CompetitiveStrategy integration** — The planned [CompetitiveStrategy](negotiation.md#competitivestrategy-coming-soon) leverages multi-seller context for harder bargains
- **Portfolio constraints** — Enforce minimum/maximum deals per seller, per channel, and per DMA
- **Seller performance tracking** — Track historical win rates, delivery rates, and pricing trends per seller
- **Graceful degradation** — If a seller is unreachable or rejects a booking, the orchestrator reallocates to remaining sellers

## Relationship to Existing Multi-Seller Discovery

| Capability | [Multi-Seller Discovery](multi-seller.md) | Multi-Seller Orchestration |
|------------|------------------------------------------|---------------------------|
| Discover sellers | Yes | Yes (uses same registry client) |
| Browse media kits | Yes | Yes (uses same media kit client) |
| Compare pricing | Yes (manual) | Yes (automated scoring) |
| Negotiate | Yes (individual) | Yes (coordinated, cross-seller) |
| Portfolio optimization | No | Yes |
| Coordinated booking | No | Yes |
| Seller performance tracking | No | Yes |

## Related

- [Multi-Seller Discovery](multi-seller.md) — Foundation: discovering and comparing sellers
- [Negotiation](negotiation.md) — Individual negotiation strategies (SimpleThreshold, and planned Adaptive/Competitive)
- [Architecture Overview](../architecture/overview.md) — Agent hierarchy including Portfolio Manager
- [Deals API](../api/deals.md) — Quote-then-book flow used by the orchestrator
- [Campaign Brief to Deal Pipeline](campaign-pipeline.md) — End-to-end campaign execution (uses orchestration internally)
