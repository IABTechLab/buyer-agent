# Creative Management

!!! info "Coming Soon — Phase 2"
    The Creative Management sub-agent (buyer-3aa) is part of Phase 2: Campaign Intelligence. This capability is independent of the deal flow track and has no Phase 1 dependencies. This page describes the planned creative management functionality.

The Creative Agent is a Level 3 sub-agent in the buyer's [agent hierarchy](../architecture/overview.md) responsible for managing creative assets throughout their lifecycle. It validates creative specs against IAB standards, manages creative rotation and A/B testing, and enforces compliance requirements before creatives are attached to deals.

## What Creative Management Does

Today, creative asset management happens outside the buyer agent — creatives are prepared in external tools and referenced by ID when booking deals. The Creative Agent brings creative management into the buyer's workflow, ensuring that creatives are validated, organized, and ready for serving before deals go live.

The Creative Agent handles three core responsibilities:

1. **Spec validation** — Verify that creative assets conform to IAB standards for their format (display dimensions, video encoding, interactive capabilities)
2. **Creative organization** — Manage creative libraries, rotation rules, and A/B test configurations
3. **Compliance enforcement** — Check creatives against brand safety, regulatory, and publisher requirements before they are attached to deals

## Planned Capabilities

### IAB Spec Validation

The Creative Agent validates creative assets against IAB Technical Laboratory standards for each format:

- **Display** — Validate dimensions against [IAB New Ad Portfolio](https://www.iab.com/newadportfolio/) standard sizes (300x250, 728x90, 320x50, etc.), file size limits, and accepted file types (HTML5, static image, rich media)
- **VAST/VPAID** — Validate video creatives against [VAST 4.2](https://iabtechlab.com/standards/vast/) and [VPAID 2.0](https://iabtechlab.com/standards/vpaid/) specs: media file encoding, duration, companion ads, verification scripts, and interactive overlays
- **SIMID** — Validate interactive ad units against the [SIMID](https://iabtechlab.com/standards/simid/) (Secure Interactive Media Interface Definition) standard for interactive video overlays and rich media experiences

### Creative Rotation

- **Weighted rotation** — Assign delivery weights to creatives within a deal (e.g., 60% Creative A, 40% Creative B)
- **Sequential rotation** — Deliver creatives in a fixed order for storytelling campaigns
- **Even rotation** — Distribute impressions equally across creatives
- **Time-based rotation** — Swap creatives based on daypart or flight phase

### A/B Testing

- **Test configuration** — Define test groups, traffic splits, and success metrics
- **Statistical tracking** — Monitor performance differences between creative variants
- **Winner selection** — Identify the winning variant based on configurable KPIs (CTR, completion rate, viewability)
- **Automatic promotion** — Optionally shift 100% of traffic to the winning creative after reaching statistical significance

### Compliance Checks

- **Brand safety** — Verify creatives do not contain prohibited content categories
- **Publisher restrictions** — Check against publisher-specific creative requirements (e.g., no auto-play audio, max animation duration)
- **Regulatory compliance** — Validate disclosures, disclaimers, and required labels for regulated industries (pharma, financial services, alcohol)

## Key Planned Functionality

- **Creative library** — Centralized storage and metadata management for all creative assets
- **Format-aware validation** — Automatic spec checking based on creative type (display, video, interactive)
- **Pre-flight checks** — Validate creatives against deal requirements before booking
- **Rotation engine** — Flexible creative rotation with real-time weight adjustments
- **A/B test framework** — Built-in experimentation with statistical rigor
- **Compliance rules engine** — Configurable rules for brand safety and regulatory requirements
- **Creative-to-deal binding** — Attach validated creatives to deals with format compatibility verification

## Integration Points

The Creative Agent connects to several buyer agent components:

- [Architecture Overview](../architecture/overview.md) — Creative Agent's position in the agent hierarchy
- [Deals API](../api/deals.md) — Creatives are attached to deals after validation
- [Campaign Brief to Deal Pipeline](campaign-pipeline.md) — Pipeline can auto-assign creatives from the library to deals based on format and channel

## Related

- [Architecture Overview](../architecture/overview.md) — Agent hierarchy and system design
- [Deals API](../api/deals.md) — Deal booking and management
- [Seller Agent Docs](https://iabtechlab.github.io/seller-agent/) — Seller-side creative requirements
