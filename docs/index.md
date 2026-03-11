# Ad Buyer Agent

The Ad Buyer Agent is an automated advertising buying system built on [CrewAI](https://crewai.com/) and the [IAB OpenDirect 2.1](https://iabtechlab.com/standards/opendirect/) protocol. It receives a campaign brief, allocates budget across channels, researches seller inventory, builds recommendations, and books deals -- all through a single API.

Part of the IAB Tech Lab Agent Ecosystem -- see also the [Seller Agent](https://iabtechlab.github.io/seller-agent/).

## Key Capabilities

- **Campaign briefing** -- accept structured campaign briefs with objectives, budget, dates, audience, and KPIs.
- **Budget allocation** -- a portfolio-manager agent splits budget across channels (branding, CTV, mobile, performance).
- **Inventory research** -- channel-specialist agents query seller product catalogs via MCP and A2A protocols.
- **Recommendation consolidation** -- recommendations from all channels are ranked and presented for review.
- **Human approval** -- optional approval checkpoint before committing spend.
- **Deal booking** -- approved recommendations are booked via the quote-then-book deal flow (IAB Deals API v1.0).
- **Multi-turn negotiation** -- pluggable negotiation strategies (anchor, split-the-difference, walk-away) over A2A conversations.
- **Multi-seller discovery** -- discover and compare sellers via AAMP registry with trust verification and capability filtering.
- **Tiered identity strategy** -- four pricing tiers (seat, agency, advertiser, anonymous) determine rate cards and discounts.
- **Persistent session management** -- sessions track conversation state, negotiation history, and deal context across interactions.
- **Linear TV buying** -- scatter and upfront buying with DMA-level targeting, CPP/CPM pricing, and daypart selection.
- **Media kit browsing** -- progressive disclosure of seller inventory from summary through full product details and pricing.

## Communication Protocols

The buyer agent communicates with seller agents using three protocols:

| Protocol | Use Case | Speed |
|----------|----------|-------|
| **[MCP](api/mcp-client.md)** | Automated tool calls -- structured, deterministic | Fast |
| **[A2A](api/a2a-client.md)** | Conversational discovery & negotiation | Moderate |
| **[REST](api/overview.md)** | Operator dashboards, legacy integration | Fast |

CrewAI tools use MCP by default. A2A is used for discovery and complex negotiations.
See [Protocol Overview](api/protocols.md) for detailed comparison.

## API Endpoints

The buyer agent exposes 7 endpoints across 3 categories:

| Category | Endpoints |
|----------|-----------|
| **Health** | `GET /health` |
| **Bookings** | `POST /bookings`, `GET /bookings/{job_id}`, `POST /bookings/{job_id}/approve`, `POST /bookings/{job_id}/approve-all`, `GET /bookings` |
| **Products** | `POST /products/search` |

See the [API Overview](api/overview.md) for full details.

## Documentation

### Getting Started

- [Quickstart](getting-started/quickstart.md) -- install, configure, and run your first booking

### Architecture & Reference

- [Agent Hierarchy](architecture/agent-hierarchy.md) -- portfolio manager, channel specialists, and tool agents
- [Tools Reference](architecture/tools.md) -- all CrewAI tools available to agents
- [Configuration](guides/configuration.md) -- environment variables, seller connections, and feature flags
- [API Reference](api/overview.md) -- all endpoints, models, and curl examples
- [Protocol Overview](api/protocols.md) -- comparison of MCP, A2A, and REST

### Guides

- [Negotiation](guides/negotiation.md) -- multi-turn negotiation strategies and deal flow
- [Identity Strategy](guides/identity.md) -- tiered pricing and buyer identity resolution
- [Sessions](guides/sessions.md) -- persistent session management across interactions
- [Multi-Seller Discovery](guides/multi-seller.md) -- AAMP registry and trust verification
- [Linear TV Buying](guides/linear-tv.md) -- scatter, upfront, DMA targeting, and CPP/CPM pricing
- [Media Kit Browsing](guides/media-kit.md) -- progressive disclosure of seller inventory
- [Deal Booking](guides/deal-booking.md) -- end-to-end quote-then-book workflow

### Integration

- [MCP Client](api/mcp-client.md) -- structured tool calls to seller agents
- [A2A Client](api/a2a-client.md) -- conversational discovery and negotiation
- [Seller Agent Integration](integration/seller-agent.md) -- connecting to seller agents and the OpenDirect protocol

### Planned Features

- [Multi-Seller Orchestration](guides/multi-seller-orchestration.md) -- cross-seller campaign optimization
- [Campaign Pipeline](guides/campaign-pipeline.md) -- end-to-end campaign lifecycle
- [Budget Pacing](guides/budget-pacing.md) -- real-time spend management
- [Creative Management](guides/creative-management.md) -- asset upload and assignment
- [Order State Machine](architecture/state-machine.md) -- formal order status transitions
- [Event Bus](architecture/event-bus.md) -- inter-agent event routing

## Links

- [Seller Agent Documentation](https://iabtechlab.github.io/seller-agent/)
- [IAB OpenDirect 2.1 Specification](https://iabtechlab.com/standards/opendirect/)
- [IAB Tech Lab](https://iabtechlab.com/)
