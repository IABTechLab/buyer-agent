# Campaign Brief to Deal Pipeline

The campaign pipeline transforms a structured **campaign brief** into **booked deals** --- automating the entire workflow from audience planning through inventory discovery, pricing, negotiation, and booking. Hand the buyer agent a brief describing what you want to achieve, and it returns a portfolio of booked deals that satisfy the brief's objectives.

!!! info "Coming Soon --- Phase 2"
    The campaign pipeline is part of Phase 2: Campaign Intelligence. The foundation it builds on --- authentication, seller discovery, media kit browsing, negotiation, and the deals API --- is all shipped and documented.

The pipeline orchestrates five stages: audience planning, inventory discovery across multiple sellers, pricing and negotiation, portfolio optimization, and deal booking. It builds on the existing [Multi-Seller Discovery](multi-seller.md), [Deals API](../api/deals.md), and [Negotiation](negotiation.md) capabilities.

## Related

- [Multi-Seller Discovery](multi-seller.md) --- Manual multi-seller workflow (the pipeline automates this)
- [Deals API](../api/deals.md) --- The underlying quote-then-book API
- [Budget Pacing & Reallocation](budget-pacing.md) --- Mid-flight budget management (builds on the pipeline)
- [Negotiation](negotiation.md) --- Automated multi-turn price negotiation
