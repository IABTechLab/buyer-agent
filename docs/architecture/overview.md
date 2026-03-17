# Architecture Overview

The buyer agent is a multi-layer system that combines a FastAPI service layer with CrewAI agent orchestration and an [OpenDirect](https://iabtechlab.com/standards/opendirect/) protocol client.

The architecture separates concerns into three layers: an HTTP API that accepts campaign briefs and exposes status, a flow engine that orchestrates the buying process through well-defined states, and a set of protocol clients that talk to seller agents. This separation means you can change how the buyer communicates with sellers (MCP, A2A, or REST) without touching the flow logic, and you can extend the flow without modifying the API surface.

## System Architecture

```mermaid
graph TB
    User["User / Campaign Manager"]

    subgraph BuyerAgent["Ad Buyer Agent"]
        API["FastAPI API Layer<br/>(port 8001)"]
        Flow["CrewAI Flow Engine<br/>(DealBookingFlow)"]

        subgraph Agents["CrewAI Agent Hierarchy"]
            Portfolio["Portfolio Manager Agent<br/>(budget allocation)"]
            Branding["Branding Specialist<br/>(display/video)"]
            CTV["CTV Specialist<br/>(streaming)"]
            Mobile["Mobile Specialist<br/>(app inventory)"]
            Performance["Performance Specialist<br/>(remarketing)"]
        end

        subgraph Clients["Protocol Clients"]
            Unified["UnifiedClient<br/>(protocol switch)"]
            MCPClient["IABMCPClient<br/>(MCP SDK / SSE)"]
            A2AClient["A2AClient<br/>(JSON-RPC 2.0)"]
            ODClient["OpenDirect Client<br/>(httpx)"]
        end

        State["Flow State<br/>(BookingState)"]
    end

    subgraph SellerAgent["Seller Agent"]
        SellerMCP["MCP Server<br/>(/mcp/sse)"]
        SellerA2A["A2A Server<br/>(/a2a/*/jsonrpc)"]
        SellerAPI["OpenDirect 2.1 API"]
        Catalog["Product Catalog"]
        Booking["Booking Engine"]
    end

    User -->|"campaign brief"| API
    API -->|"background task"| Flow
    Flow --> Portfolio
    Portfolio -->|"channel budgets"| Branding
    Portfolio -->|"channel budgets"| CTV
    Portfolio -->|"channel budgets"| Mobile
    Portfolio -->|"channel budgets"| Performance
    Branding --> Unified
    CTV --> Unified
    Mobile --> Unified
    Performance --> Unified
    Unified -->|"structured tools"| MCPClient
    Unified -->|"natural language"| A2AClient
    MCPClient -->|"MCP / SSE"| SellerMCP
    A2AClient -->|"JSON-RPC"| SellerA2A
    SellerMCP --> Catalog
    SellerMCP --> Booking
    SellerA2A --> Catalog
    SellerA2A --> Booking
    Flow --> State
    ODClient -->|"OpenDirect HTTP"| SellerAPI
    SellerAPI --> Catalog
    SellerAPI --> Booking
    API -->|"status / results"| User
```

## Architecture at a Glance

The Architecture section covers these topics:

| Topic | What It Covers |
|-------|---------------|
| **[Agent Hierarchy](agent-hierarchy.md)** | Three-level agent structure: portfolio manager, channel specialists, and tool-level agents |
| **[Booking Flow](booking-flow.md)** | Detailed sequence diagram of the DealBookingFlow --- the campaign-level orchestration |
| **[DSP Deal Flow](dsp-deal-flow.md)** | Single-deal flow for direct DSP integration without multi-channel orchestration |
| **[Order State Machine](state-machine.md)** | 12 deal states and 9 campaign states with guard conditions and audit trail |
| **[Event Bus](event-bus.md)** | 13 event types providing structured observability across all flows |
| **[Deal Store](deal-store.md)** | SQLite persistence for deals, events, and session state |
| **[Models](models.md)** | Pydantic data models for API requests, flow state, and deal records |
| **[Tools Reference](tools.md)** | CrewAI tools available to agents for research, booking, and negotiation |

### Two Entry Points: Campaign Flow vs. Deal Flow

The buyer has two distinct flow entry points, depending on the use case:

- **DealBookingFlow** (campaign flow) --- Starts from a campaign brief. The portfolio manager allocates budget across channels, channel specialists research inventory in parallel, recommendations are built and approved, then deals are booked. This is the multi-channel, orchestrated path.
- **DSPDealFlow** (deal flow) --- Starts from a single deal request. Discovers inventory, evaluates pricing, and books one deal directly. This is the lightweight, single-deal path used for DSP integration.

Both flows share the same deal state machine, event bus, and DealStore persistence --- they differ in scope and orchestration, not in how individual deals are managed.

## Component Summary

| Component | Role | Key File |
|-----------|------|----------|
| **FastAPI API** | HTTP endpoints, authentication, job management | `interfaces/api/main.py` |
| **DealBookingFlow** | Event-driven CrewAI flow orchestrating the full booking lifecycle | `flows/deal_booking_flow.py` |
| **Portfolio Manager Agent** | Allocates budget across channels based on objectives | `crews/portfolio_crew.py` |
| **Channel Specialist Agents** | Research inventory and build recommendations per channel | `crews/channel_crews.py` |
| **UnifiedClient** | Protocol-switching client for MCP and A2A seller communication | `clients/unified_client.py` |
| **IABMCPClient** | MCP SDK client with Streamable HTTP transport | `clients/mcp_client.py` |
| **A2AClient** | JSON-RPC 2.0 client for conversational agent-to-agent requests | `clients/a2a_client.py` |
| **OpenDirectClient** | Async HTTP client for IAB OpenDirect 2.1 seller APIs | `clients/opendirect_client.py` |
| **NegotiationClient** | Multi-turn price negotiation with seller agents via A2A/proposals | `clients/negotiation_client.py` |
| **BookingState** | Pydantic state model tracking the full flow lifecycle | `models/flow_state.py` |
| **Settings** | Environment-based configuration via pydantic-settings | `config/settings.py` |

## Seller Communication Protocols

The buyer communicates with seller agents through three protocols, managed by the `UnifiedClient`:

```
CrewAI Tools --> UnifiedClient --> IABMCPClient --> Seller MCP Server (/mcp/sse) --> Seller Tools
CrewAI Tools --> UnifiedClient --> A2AClient   --> Seller A2A Server (/a2a/*/jsonrpc) --> NL Processing --> Seller Tools
Human        --> REST API      --> Buyer Agent  --> (MCP or A2A) --> Seller Agent
```

| Protocol | Transport | Use Case |
|----------|-----------|----------|
| **MCP** | Streamable HTTP (SSE) | Automated workflows --- structured tool calls, deterministic |
| **A2A** | JSON-RPC 2.0 | Discovery and negotiation --- natural language, multi-turn |
| **REST** | Standard HTTP | Operator dashboards and legacy integration |

MCP is the default. The `UnifiedClient` can switch protocols per-request or operate in dual-protocol mode via `connect_both()`.

See [Protocol Overview](../api/protocols.md) for full details.

## Full Ecosystem

```mermaid
graph LR
    CM["Campaign Manager"]
    Buyer["Ad Buyer Agent<br/>(port 8001)"]
    Seller["Ad Seller Agent<br/>(port 8000)"]
    Pub["Publisher Inventory"]

    CM -->|"brief + approve"| Buyer
    Buyer -->|"MCP / A2A / OpenDirect"| Seller
    Seller -->|"catalog + booking"| Pub
    Seller -->|"results + confirmations"| Buyer
    Buyer -->|"status + booked lines"| CM
```

The buyer agent acts as an automated media buyer. It receives campaign requirements from a user or campaign manager, uses AI agents to plan and research, negotiates pricing for eligible buyer tiers, and executes deals against one or more seller agents using MCP (primary), A2A (conversational), or OpenDirect 2.1 REST (legacy).

See also: [Seller Agent Architecture](https://iabtechlab.github.io/seller-agent/architecture/overview/)

## Related

- [Booking Flow](booking-flow.md) --- detailed sequence diagram of the campaign-level DealBookingFlow
- [DSP Deal Flow](dsp-deal-flow.md) --- single-deal flow for direct DSP integration
- [Order State Machine](state-machine.md) --- deal and campaign lifecycle enforcement
- [Event Bus](event-bus.md) --- structured observability across all flows
- [Models](models.md) --- data model reference
- [Seller Agent Integration](../integration/seller-agent.md)
