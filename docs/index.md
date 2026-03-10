# Ad Buyer Agent

The Ad Buyer Agent is an automated advertising buying system built on [CrewAI](https://crewai.com/) and the [IAB OpenDirect 2.1](https://iabtechlab.com/standards/opendirect/) protocol. It receives a campaign brief, allocates budget across channels, researches seller inventory, builds recommendations, and books deals -- all through a single API.

Part of the IAB Tech Lab Agent Ecosystem -- see also the [Seller Agent](https://iabtechlab.github.io/seller-agent/).

## Key Capabilities

- **Campaign briefing** -- accept structured campaign briefs with objectives, budget, dates, audience, and KPIs.
- **Budget allocation** -- a portfolio-manager agent splits budget across channels (branding, CTV, mobile, performance).
- **Inventory research** -- channel-specialist agents query seller product catalogs via OpenDirect.
- **Recommendation consolidation** -- recommendations from all channels are ranked and presented for review.
- **Human approval** -- optional approval checkpoint before committing spend.
- **Deal booking** -- approved recommendations are booked as line items against seller APIs.

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

- [Quickstart](getting-started/quickstart.md) -- install, configure, and run your first booking
- [API Reference](api/overview.md) -- all endpoints, models, and curl examples
- [Architecture](architecture/overview.md) -- system design, agent hierarchy, and flow diagrams
- [MCP Client](api/mcp-client.md) -- structured tool calls to seller agents
- [A2A Client](api/a2a-client.md) -- conversational discovery and negotiation
- [Protocol Overview](api/protocols.md) -- comparison of MCP, A2A, and REST
- [Integration](integration/seller-agent.md) -- connecting to seller agents and the OpenDirect protocol

## Links

- [Seller Agent Documentation](https://iabtechlab.github.io/seller-agent/)
- [IAB OpenDirect 2.1 Specification](https://iabtechlab.com/standards/opendirect/)
- [IAB Tech Lab](https://iabtechlab.com/)
