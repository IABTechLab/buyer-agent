# Creative Management

The Creative Agent is a Level 3 sub-agent in the buyer's [agent hierarchy](../architecture/overview.md) responsible for managing creative assets throughout their lifecycle. It validates creative specs against IAB standards, manages creative rotation and A/B testing, and enforces compliance requirements before creatives are attached to deals.

!!! info "Coming Soon --- Phase 2"
    The Creative Management sub-agent is part of Phase 2: Campaign Intelligence. This capability is independent of the deal flow track and has no Phase 1 dependencies.

The Creative Agent handles three core responsibilities: spec validation (verifying creatives conform to IAB standards for display, VAST/VPAID, and SIMID formats), creative organization (libraries, rotation rules, and A/B test configurations), and compliance enforcement (brand safety, publisher restrictions, and regulatory requirements).

## Related

- [Architecture Overview](../architecture/overview.md) --- Agent hierarchy and system design
- [Deals API](../api/deals.md) --- Deal booking and management
- [Campaign Brief to Deal Pipeline](campaign-pipeline.md) --- Pipeline can auto-assign creatives from the library to deals
- [Seller Agent Docs](https://iabtechlab.github.io/seller-agent/) --- Seller-side creative requirements
