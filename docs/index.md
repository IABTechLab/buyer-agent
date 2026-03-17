# Ad Buyer Agent

The Ad Buyer Agent is an automated advertising buying system built on [CrewAI](https://crewai.com/) and the [IAB OpenDirect 2.1](https://iabtechlab.com/standards/opendirect/) protocol. It receives a campaign brief, allocates budget across channels, researches seller inventory, builds recommendations, and books deals --- all through a single API.

You give the buyer a campaign brief describing what you want to buy. The agent does the rest: it discovers sellers, browses their inventory, negotiates pricing where your access tier allows, and books deals --- pausing for your approval before committing spend. The result is a set of confirmed order lines with full audit trail, ready for delivery.

Part of the IAB Tech Lab Agent Ecosystem --- see also the [Seller Agent](https://iabtechlab.github.io/seller-agent/).

!!! note "Alpha Release"
    The buyer agent is in active development. Core deal flow (brief, research, negotiate, book) is functional end-to-end. See [PROGRESS.md](https://github.com/IABTechLab/buyer-agent/blob/main/.beads/PROGRESS.md) for current roadmap status.

## Key Capabilities

**Discovery & Research**

- Multi-seller discovery via [AAMP](https://iabtechlab.com/standards/aamp-agentic-advertising-management-protocols/) registry with trust verification and capability filtering
- Channel-specialist agents research seller catalogs via MCP and A2A protocols
- Progressive media-kit browsing from summary through full product details and pricing

**Negotiation & Pricing**

- Tiered identity strategy with 4 access tiers (public, seat, agency, advertiser) and progressive rate discounts
- Multi-turn negotiation with pluggable strategies (threshold, adaptive, competitive) over A2A conversations
- Human-in-the-loop approval gate before committing spend

**Execution & Booking**

- Structured campaign briefing with objectives, budget, dates, audience, and KPIs
- Portfolio-manager agent splits budget across 4 channels (branding, CTV, mobile, performance)
- Quote-then-book deal flow via IAB Deals API v1.0 with DealStore persistence
- Linear TV scatter buying with DMA-level targeting, CPP/CPM pricing, and daypart selection

**Observability & Lifecycle**

- Formal order state machine with 12 deal states, guard conditions, and audit trail
- Event bus with 13 event types, fail-open emission, subscriber dispatch, and SQLite persistence
- Persistent session management tracking conversation state, negotiation history, and deal context
- Severity-based change request management for post-deal modifications

## Access Methods

The buyer agent communicates with seller agents using three protocols:

| Protocol | Use Case | Speed |
|----------|----------|-------|
| **[MCP](api/mcp-client.md)** | Automated tool calls --- structured, deterministic | Fast |
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

- [Quickstart](getting-started/quickstart.md) --- install, configure, run, and connect to a seller agent

### Architecture & Reference

- [Agent Hierarchy](architecture/agent-hierarchy.md) --- portfolio manager, channel specialists, and tool agents
- [Tools Reference](architecture/tools.md) --- all CrewAI tools available to agents
- [Configuration](guides/configuration.md) --- environment variables, seller connections, and feature flags
- [API Reference](api/overview.md) --- all endpoints, models, and curl examples
- [Protocol Overview](api/protocols.md) --- comparison of MCP, A2A, and REST
- [Order State Machine](architecture/state-machine.md) --- 12 deal states with guard conditions and audit trail
- [Event Bus](architecture/event-bus.md) --- 13 event types with fail-open emission and persistence

### Guides

- [Buyer Guide Overview](guides/overview.md) --- end-to-end buyer workflow orientation and topic map
- [Deal Booking](guides/deal-booking.md) --- end-to-end quote-then-book workflow
- [Negotiation](guides/negotiation.md) --- multi-turn negotiation strategies and deal flow
- [Identity Strategy](guides/identity.md) --- tiered pricing and buyer identity resolution
- [Media Kit Browsing](guides/media-kit.md) --- progressive disclosure of seller inventory
- [Sessions](guides/sessions.md) --- persistent session management across interactions
- [Multi-Seller Discovery](guides/multi-seller.md) --- AAMP registry and trust verification
- [Linear TV Buying](guides/linear-tv.md) --- scatter, upfront, DMA targeting, and CPP/CPM pricing

### Integration

- [MCP Client](api/mcp-client.md) --- structured tool calls to seller agents
- [A2A Client](api/a2a-client.md) --- conversational discovery and negotiation
- [Seller Agent Integration](integration/seller-agent.md) --- connecting to seller agents and the OpenDirect protocol

