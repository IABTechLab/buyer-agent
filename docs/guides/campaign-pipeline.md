# Campaign Brief to Deal Pipeline

!!! info "Coming Soon — Phase 2"
    The campaign pipeline (buyer-u8l) is part of Phase 2: Campaign Intelligence. The foundation it builds on — authentication, seller discovery, media kit browsing, negotiation, and the deals API — is all shipped and documented. This page describes the planned end-to-end pipeline capability.

The campaign pipeline transforms a structured **campaign brief** into **booked deals** — automating the entire workflow from audience planning through inventory discovery, pricing, negotiation, and booking. This is the "one-click campaign" capability: hand the buyer agent a brief describing what you want to achieve, and it returns a portfolio of booked deals that satisfy the brief's objectives.

## What the Pipeline Does

Today, assembling a media plan requires manually stepping through each stage: discover sellers, browse media kits, request quotes, negotiate pricing, evaluate options, and book deals. The campaign pipeline orchestrates all of these stages automatically.

Given a campaign brief, the pipeline will:

1. **Parse the brief** — Extract target audience, budget, channels, flight dates, and KPIs from a structured JSON input
2. **Plan the audience** — Map the brief's audience definition to targeting parameters using the Universal Campaign Planner (UCP)
3. **Discover inventory** — Query the seller registry and browse media kits across multiple sellers, filtering for inventory that matches the brief's channel and audience requirements
4. **Request pricing** — Submit quote requests to qualifying sellers via the [Deals API](../api/deals.md)
5. **Negotiate** — Run automated [negotiation](negotiation.md) with sellers whose quotes are within budget range
6. **Optimize the portfolio** — Select the combination of deals that maximizes reach and efficiency within budget constraints
7. **Book deals** — Convert selected quotes into confirmed deals

## Campaign Brief Structure

The pipeline accepts a JSON campaign brief as input. The brief describes the advertiser's goals without prescribing specific sellers or packages.

```json
{
  "name": "Q3 2026 Back-to-School Campaign",
  "advertiser": "target-stores-001",
  "objectives": ["brand_awareness", "reach"],
  "budget": {
    "total_usd": 250000,
    "channel_allocation": {
      "ctv": 0.40,
      "display": 0.35,
      "mobile": 0.25
    }
  },
  "audience": {
    "demographics": {
      "age_range": "25-54",
      "gender": "all",
      "hhi_min": 50000
    },
    "interests": ["parenting", "education", "shopping"],
    "geo": {
      "country": "US",
      "dma_codes": ["501", "803", "602"]
    }
  },
  "flight": {
    "start_date": "2026-07-15",
    "end_date": "2026-09-15"
  },
  "kpis": {
    "target_cpm": 18.00,
    "max_cpm": 30.00,
    "min_reach_pct": 60
  },
  "constraints": {
    "max_sellers": 5,
    "min_deals_per_channel": 1,
    "brand_safety": ["standard"]
  }
}
```

## Planned Pipeline Stages

### Stage 1: Audience Planning

The pipeline translates the brief's high-level audience description into targeting parameters that can be matched against seller inventory. This includes mapping demographic targets to IAB audience segments and resolving geographic constraints to DMA codes.

### Stage 2: Inventory Discovery

Using the [Multi-Seller Discovery](multi-seller.md) workflow, the pipeline queries the seller registry for sellers that carry relevant inventory, browses their media kits, and builds a candidate set of packages that match the brief's channel and audience requirements.

### Stage 3: Pricing and Negotiation

The pipeline requests quotes from candidate sellers via the [Deals API](../api/deals.md) and runs automated negotiation using configurable [negotiation strategies](negotiation.md). The negotiation strategy can be set per-channel or per-seller.

### Stage 4: Portfolio Optimization

With quotes and negotiated prices in hand, the pipeline selects the optimal combination of deals. The optimizer balances price efficiency against reach, respects budget constraints and channel allocations, and ensures minimum deal counts per channel.

### Stage 5: Booking

Selected deals are booked through the standard [Deals API](../api/deals.md) quote-then-book flow. The pipeline returns a summary of all booked deals, including deal IDs, pricing, and activation instructions.

## Key Planned Functionality

- **Brief-driven execution** — Define campaign goals declaratively; the pipeline handles tactical execution
- **Multi-channel orchestration** — Allocate budget across CTV, display, and mobile channels within a single pipeline run
- **Automatic seller selection** — Discover and evaluate sellers based on brief requirements, not manual shortlists
- **Budget-aware negotiation** — Negotiation strategies informed by the brief's target and maximum CPM
- **Portfolio-level optimization** — Select deals that maximize campaign objectives, not just minimize individual CPMs
- **Approval gates** — Optional human-in-the-loop checkpoints before booking (configurable via `auto_approve`)
- **Idempotent execution** — Re-running the pipeline with the same brief produces consistent results

## Integration Points

The campaign pipeline builds on existing buyer agent capabilities:

- [Seller Discovery](../api/seller-discovery.md) — Registry-based seller lookup
- [Media Kit Browsing](media-kit.md) — Inventory discovery across sellers
- [Deals API](../api/deals.md) — Quote-then-book flow for pricing and booking
- [Negotiation](negotiation.md) — Automated multi-turn price negotiation
- [Identity Strategy](identity.md) — Per-seller identity disclosure decisions
- [Sessions](sessions.md) — Persistent conversation context with sellers

## Related

- [Multi-Seller Discovery](multi-seller.md) — Manual multi-seller workflow (the pipeline automates this)
- [Deals API](../api/deals.md) — The underlying quote-then-book API
- [Budget Pacing & Reallocation](budget-pacing.md) — Mid-flight budget management (builds on the pipeline)
