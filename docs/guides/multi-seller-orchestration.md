# Multi-Seller Deal Orchestration

Multi-seller deal orchestration extends the buyer agent from shopping multiple sellers individually to **coordinating deals across sellers as a unified portfolio**. Where the existing [multi-seller discovery](multi-seller.md) guide covers finding and comparing inventory, orchestration adds strategic deal execution: running parallel negotiations, applying cross-seller optimization, and managing a portfolio of deals as a coordinated whole.

!!! info "Coming Soon --- Phase 2"
    Multi-seller deal orchestration is part of Phase 2: Campaign Intelligence. For the existing multi-seller *discovery and shopping* workflow, see [Multi-Seller Discovery](multi-seller.md).

The orchestration layer adds parallel quote requests across sellers, cross-seller negotiation strategy (using competing offers as leverage), Portfolio Manager agent coordination for optimal deal selection, and atomic portfolio booking with rollback handling.

## Related

- [Multi-Seller Discovery](multi-seller.md) --- Foundation: discovering and comparing sellers
- [Negotiation](negotiation.md) --- Individual negotiation strategies (SimpleThreshold, and planned Adaptive/Competitive)
- [Architecture Overview](../architecture/overview.md) --- Agent hierarchy including Portfolio Manager
- [Deals API](../api/deals.md) --- Quote-then-book flow used by the orchestrator
- [Campaign Brief to Deal Pipeline](campaign-pipeline.md) --- End-to-end campaign execution (uses orchestration internally)
