> **V2 — Active Development**

# IAB Tech Lab — Buyer Agent

An AI-powered media buying system for **DSPs, agencies, and advertisers** to automate programmatic direct purchases using IAB OpenDirect 2.1 standards.

**[Full Documentation →](https://iabtechlab.github.io/buyer-agent/)**

## What This Does

- **Browse seller media kits** with public (price ranges) or authenticated (exact pricing) access
- **Automate deal negotiations** with configurable strategies (target CPM, max CPM, concession limits)
- **Book deals programmatically** via IAB OpenDirect 2.1 protocol
- **Obtain Deal IDs for DSP activation** in The Trade Desk, DV360, Amazon DSP, and other platforms
- **Present buyer identity** (seat, agency, advertiser) to unlock tiered pricing from sellers
- **Aggregate inventory** across multiple sellers in parallel

## Who Should Use This

- **Media agencies** automating programmatic direct buying with identity-based pricing
- **Advertisers** with in-house teams seeking direct seller relationships
- **DSP operators** discovering inventory and obtaining Deal IDs for activation
- **Trading desks** scaling deal operations across multiple sellers

## Access Methods

The buyer agent communicates with sellers via three protocols:

| Interface | Use Case |
|-----------|----------|
| **MCP Client** | Primary — structured tool calls to seller MCP servers |
| **A2A Client** | Conversational — JSON-RPC 2.0 for natural language queries |
| **REST Client** | Direct — HTTP calls for admin and OpenDirect operations |

→ [Protocol Documentation](https://iabtechlab.github.io/buyer-agent/api/protocols/)

## Architecture

```
Campaign Brief ──→ Portfolio Manager (Opus)
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
    Channel Specialists (Sonnet)
    Branding │ CTV │ Mobile │ Performance │ Deals
          │              │              │
          ▼              ▼              ▼
    Functional Agents (Sonnet)
    Research │ Execution │ Reporting │ Audience
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
    MediaKitClient  NegotiationClient  OpenDirectClient
    (browse sellers) (multi-turn deals) (book orders)
          │              │              │
          ▼              ▼              ▼
                   Seller Agents
              (MCP / A2A / REST)
```

→ [Architecture Documentation](https://iabtechlab.github.io/buyer-agent/architecture/overview/)

## Key Features

### Media Kit Discovery
Browse seller inventory catalogs. Unauthenticated access shows price ranges; authenticate with an API key for exact pricing, placements, and audience segments. Aggregate across multiple sellers in parallel.

```python
from ad_buyer.media_kit import MediaKitClient

async with MediaKitClient(api_key="your-key") as client:
    kit = await client.get_media_kit("http://seller.example.com:8001")
    for pkg in kit.featured:
        print(f"{pkg.name}: ${pkg.price_range}")
```

→ [Media Kit Documentation](https://iabtechlab.github.io/buyer-agent/api/media-kit/)

### Negotiation

Pluggable strategy pattern for multi-turn price negotiation. Auto-negotiate or drive step-by-step.

```python
from ad_buyer.negotiation.client import NegotiationClient
from ad_buyer.negotiation.strategies.simple_threshold import SimpleThresholdStrategy

strategy = SimpleThresholdStrategy(
    target_cpm=20.0,       # Opening offer
    max_cpm=30.0,          # Accept anything at or below
    concession_step=2.0,   # Concede $2/round
    max_rounds=5,          # Walk away after 5 rounds
)

client = NegotiationClient(api_key="your-key")
result = await client.auto_negotiate(seller_url, proposal_id, strategy)
print(f"Outcome: {result.outcome}, Price: ${result.final_price}")
```

| Strategy | Status | Description |
|----------|--------|-------------|
| SimpleThresholdStrategy | **Available** | Fixed thresholds + linear concession |
| AdaptiveStrategy | Planned | Adjusts based on seller patterns |
| CompetitiveStrategy | Planned | Multi-seller competitive bidding |

→ [Negotiation Guide](https://iabtechlab.github.io/buyer-agent/guides/negotiation/)

### Identity-Based Tiered Pricing

Reveal buyer identity progressively to unlock better pricing from sellers:

| Tier | Identity Required | Typical Discount | Negotiation |
|------|-------------------|:----------------:|:-----------:|
| **Public** | None | 0% (range only) | — |
| **Seat** | API key | ~5% | — |
| **Agency** | Agency ID | ~10% | Yes |
| **Advertiser** | Advertiser ID | ~15% | Yes |

→ [Authentication Guide](https://iabtechlab.github.io/buyer-agent/api/authentication/)

### Vendor Approval Gating (optional)

Plug in an [IAB Diligence Platform](https://safeguardprivacy.com) tenant to keep unapproved sellers out of the buyer-agent workflow. Consults the `iabBuyerAgentApproval` flag via SGP's integration API. When `SGP_ENFORCE=true`, `DiscoverInventoryTool` filters NOT APPROVED vendors out of search results before the agent ever sees them, and `RequestDealTool` enforces the same check as a safety net at Deal ID time. With enforcement off, products are annotated APPROVED / NOT APPROVED / UNKNOWN but never filtered. Off by default — inert when `SGP_API_KEY` is empty.

→ [IAB Diligence Platform Approval](https://iabtechlab.github.io/buyer-agent/integration/iab-diligence-platform/)

## Quick Start

### Install

```bash
git clone https://github.com/IABTechLab/buyer-agent.git
cd buyer-agent
pip install -e .
```

### Configure

```bash
cp .env.example .env
```

Key settings:

```bash
# LLM — set the API key for your chosen provider
ANTHROPIC_API_KEY=sk-ant-api03-xxxxx        # For Anthropic (default)
# OPENAI_API_KEY=sk-xxxxx                   # For OpenAI / Azure
# COHERE_API_KEY=xxxxx                      # For Cohere

# LLM model (uses provider/model format — native Anthropic, OpenAI, Gemini, Azure, Bedrock)
DEFAULT_LLM_MODEL=anthropic/claude-sonnet-4-5-20250929
# DEFAULT_LLM_MODEL=openai/gpt-4o          # OpenAI example
# DEFAULT_LLM_MODEL=ollama/llama3           # Local Ollama example

# Seller connection
SELLER_BASE_URL=http://localhost:8001        # Seller agent URL

# Storage
DATABASE_URL=sqlite:///./ad_buyer.db
```

> **LLM Provider Flexibility:** CrewAI supports native integrations with Anthropic (default), OpenAI, Google Gemini, Azure OpenAI, and AWS Bedrock. Set `DEFAULT_LLM_MODEL` and `MANAGER_LLM_MODEL` using `provider/model-name` format (e.g., `anthropic/claude-sonnet-4-5-20250929`) and provide the matching API key. Install the matching extra: `pip install "crewai[anthropic]"`. See the [Quickstart Guide](https://iabtechlab.github.io/buyer-agent/getting-started/quickstart/) for details.

→ [Full Configuration](https://iabtechlab.github.io/buyer-agent/getting-started/quickstart/)

### Run

```bash
python -m ad_buyer.interfaces.api.main
# Server runs at http://localhost:8000
```

This is equivalent to running the ASGI app directly with uvicorn:

```bash
uvicorn ad_buyer.interfaces.api.main:app --port 8000
```

`ANTHROPIC_API_KEY` is optional to *start* the server (the API boots without it);
it is only required once you run CrewAI-backed booking flows.

> **This quickstart is tested.** `tests/smoke/test_quickstart_smoke.py` boots the app at
> the exact module path documented above (`ad_buyer.interfaces.api.main:app`) through its
> real startup lifecycle and asserts `/health` and `/bookings` respond — no network or LLM
> calls. Run it with `ANTHROPIC_API_KEY=test pytest tests/smoke/test_quickstart_smoke.py`.
> If it fails, the entrypoint above is wrong.

### Verify

```bash
# Health check (served by the buyer agent itself — no backend needed)
curl http://localhost:8000/health

# List bookings (empty on a fresh server)
curl http://localhost:8000/bookings
```

The next two calls reach *outward* to a seller agent / OpenDirect backend, so they
only work once a seller agent is running (see the [Seller Agent](https://github.com/IABTechLab/seller-agent)
and `SELLER_BASE_URL` in `.env`):

```bash
# Browse a seller's media kit (requires a seller agent on :8001)
curl http://localhost:8001/media-kit

# Search products across sellers (requires a reachable seller/OpenDirect backend)
curl -X POST http://localhost:8000/products/search \
  -H "Content-Type: application/json" \
  -d '{"channel": "ctv", "limit": 5}'
```

→ [Quickstart Guide](https://iabtechlab.github.io/buyer-agent/getting-started/quickstart/)

### Campaign Automation Demo

A self-contained, browser-based walkthrough of the campaign automation flow (budget
allocation → pacing → reporting). It runs entirely in-process — no seller agent or
external services required — and needs Flask, which ships in the `dev` extra:

```bash
pip install -e ".[dev]"
python -m demo.campaign_demo
# Opens at http://localhost:5055 (override with CAMPAIGN_DEMO_PORT)
```

### Docker

Run in a container with Docker Compose:

```bash
cd infra/docker
docker compose up
```

→ [Deployment Guide](https://iabtechlab.github.io/buyer-agent/guides/deployment/)

## API Reference

11 endpoints across 5 groups:

| Group | Endpoints | Description |
|-------|-----------|-------------|
| Health | 1 | Service health check |
| Bookings | 5 | Create, list, poll, and approve bookings |
| Products | 1 | Search seller product catalog |
| Events | 2 | Query the in-memory event bus |
| Buyer Orders | 2 | Order records and status-transition audit trail |

→ [Full API Reference](https://iabtechlab.github.io/buyer-agent/api/overview/)

## Client Libraries

| Client | Purpose | Docs |
|--------|---------|------|
| `MediaKitClient` | Browse seller inventory catalogs | [Media Kit](https://iabtechlab.github.io/buyer-agent/api/media-kit/) |
| `NegotiationClient` | Multi-turn price negotiation | [Negotiation](https://iabtechlab.github.io/buyer-agent/guides/negotiation/) |
| `OpenDirectClient` | OpenDirect 2.1 booking operations | [Seller Integration](https://iabtechlab.github.io/buyer-agent/integration/seller-agent/) |
| `IABMCPClient` | MCP tool calls to seller agents | [MCP Client](https://iabtechlab.github.io/buyer-agent/api/mcp-client/) |
| `A2AClient` | Conversational JSON-RPC with sellers | [A2A Client](https://iabtechlab.github.io/buyer-agent/api/a2a-client/) |

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
ANTHROPIC_API_KEY=test pytest tests/ -v

# Lint
ruff check src/

# Build docs locally
pip install -e ".[docs]"
mkdocs serve
```

## Related

- [Seller Agent](https://github.com/IABTechLab/seller-agent) — Publisher/SSP-side agent
- [Seller Agent Docs](https://iabtechlab.github.io/seller-agent/) — Seller documentation
- [agentic-direct](https://github.com/InteractiveAdvertisingBureau/agentic-direct) — IAB Tech Lab reference implementation

## License

Apache 2.0
