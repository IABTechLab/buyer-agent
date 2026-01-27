# Ad Buyer System

An AI-powered media buying system for **DSPs, agencies, and advertisers** to automate programmatic direct purchases using IAB OpenDirect standards.

## What This Does

The Ad Buyer System lets you:

- **Automate media buying** with AI agents that understand your campaign goals and budget
- **Plan audiences** using IAB Tech Lab UCP (User Context Protocol) for real-time matching
- **Search and discover inventory** across publishers using natural language or structured queries
- **Book deals programmatically** via IAB OpenDirect 2.1 protocol
- **Obtain Deal IDs for DSP activation** - present buyer identity (agency, advertiser) to unlock tiered pricing, then get Deal IDs for activation in The Trade Desk, DV360, Amazon DSP, and other platforms
- **Manage campaigns** through CLI, REST API, or conversational chat interface
- **Connect to any OpenDirect-compliant seller** including the live IAB Tech Lab server

## Who Should Use This

- **Media agencies** automating programmatic direct buying and leveraging identity-based tiered pricing
- **Advertisers** with in-house media teams seeking better rates through direct seller relationships
- **Trading desks** looking to scale deal operations and manage Deal IDs across multiple DSPs
- **DSP operators** who need to discover inventory, negotiate pricing, and obtain Deal IDs for programmatic activation
- **Anyone** wanting to experiment with agentic advertising workflows

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            AD BUYER SYSTEM                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ╔═══════════════════════════════════════════════════════════════════════╗  │
│  ║                 LEVEL 1: ORCHESTRATION (Claude Opus)                  ║  │
│  ║  ┌─────────────────────────────────────────────────────────────────┐  ║  │
│  ║  │                    Portfolio Manager                            │  ║  │
│  ║  │   • Budget allocation            • Strategic decisions          │  ║  │
│  ║  │   • Channel optimization         • Performance management       │  ║  │
│  ║  └─────────────────────────────────────────────────────────────────┘  ║  │
│  ╚═══════════════════════════════════════════════════════════════════════╝  │
│                                    │                                        │
│                                    ▼                                        │
│  ╔═══════════════════════════════════════════════════════════════════════╗  │
│  ║             LEVEL 2: CHANNEL SPECIALISTS (Claude Sonnet)              ║  │
│  ║  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐         ║  │
│  ║  │Branding │ │ Mobile  │ │   CTV   │ │ Perfor- │ │   DSP   │         ║  │
│  ║  │  Agent  │ │App Agent│ │  Agent  │ │ mance   │ │  Agent  │         ║  │
│  ║  └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘         ║  │
│  ╚═══════════════════════════════════════════════════════════════════════╝  │
│                                    │                                        │
│                                    ▼                                        │
│  ╔═══════════════════════════════════════════════════════════════════════╗  │
│  ║              LEVEL 3: FUNCTIONAL AGENTS (Claude Sonnet)               ║  │
│  ║  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐                 ║  │
│  ║  │ Research │ │Execution │ │Reporting │ │ Audience │                 ║  │
│  ║  │  Agent   │ │  Agent   │ │  Agent   │ │ Planner  │                 ║  │
│  ║  └──────────┘ └──────────┘ └──────────┘ └──────────┘                 ║  │
│  ╚═══════════════════════════════════════════════════════════════════════╝  │
│                                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│  TOOLS                                                                      │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │ Research: ProductSearch, AvailsCheck                                   │ │
│  │ Execution: CreateOrder, CreateLine, BookLine, ReserveLine              │ │
│  │ DSP: DiscoverInventory, GetPricing, RequestDeal                        │ │
│  │ Audience: AudienceDiscovery, AudienceMatching, CoverageEstimation      │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────────────────────┤
│  INTERFACES: CLI │ REST API │ Chat                                          │
├─────────────────────────────────────────────────────────────────────────────┤
│  STORAGE: SQLite (dev) │ Redis (prod)                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│  PROTOCOLS                                                                  │
│  ┌──────────────────────┐  ┌──────────────────────┐  ┌──────────────────┐  │
│  │ MCP (33 OpenDirect   │  │ A2A (Natural Language│  │ UCP (Audience    │  │
│  │      Tools)          │  │      Queries)        │  │    Embeddings)   │  │
│  └──────────────────────┘  └──────────────────────┘  └──────────────────┘  │
├─────────────────────────────────────────────────────────────────────────────┤
│  SERVER: IAB Tech Lab agentic-direct (OpenDirect 2.1)                       │
│  https://agentic-direct-server-hwgrypmndq-uk.a.run.app                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Agent Hierarchy

| Level | Agent | Model | Temperature | Role |
|-------|-------|-------|-------------|------|
| **1** | Portfolio Manager | Claude Opus | 0.3 | Strategic orchestration, budget allocation, channel optimization |
| **2** | Branding Agent | Claude Sonnet | 0.5 | Brand awareness campaigns, premium placements |
| **2** | Mobile App Agent | Claude Sonnet | 0.5 | In-app advertising, rewarded video, interstitials |
| **2** | CTV Agent | Claude Sonnet | 0.5 | Connected TV, streaming, household targeting |
| **2** | Performance Agent | Claude Sonnet | 0.5 | Direct response, conversion optimization |
| **2** | DSP Agent | Claude Sonnet | 0.5 | Deal ID discovery, programmatic activation |
| **3** | Research Agent | Claude Sonnet | 0.2 | Inventory search, availability checking |
| **3** | Execution Agent | Claude Sonnet | 0.2 | Order creation, line booking |
| **3** | Reporting Agent | Claude Sonnet | 0.3 | Performance reporting, analytics |
| **3** | Audience Planner | Claude Sonnet | 0.3 | UCP-based audience planning, coverage estimation |

---

## UCP: User Context Protocol

The Ad Buyer System integrates with the **IAB Tech Lab User Context Protocol (UCP)** for intelligent audience planning and matching.

### What UCP Does

UCP enables real-time audience matching between buyer and seller agents by exchanging embeddings (256-1024 dimension vectors) that encode:

- **Identity Signals** - Hashed user IDs, device graphs
- **Contextual Signals** - Page content, keywords, categories
- **Reinforcement Signals** - Conversion data, feedback loops

### Audience Planner Agent

The **Audience Planner Agent** (Level 3) uses UCP to:

1. **Discover Capabilities** - Query seller audience capabilities via UCP
2. **Match Requirements** - Align campaign audience requirements to inventory
3. **Estimate Coverage** - Calculate what percentage of inventory matches the audience
4. **Identify Gaps** - Find audience requirements the seller cannot support
5. **Suggest Alternatives** - Recommend similar audiences when exact match unavailable

### Audience Tools

| Tool | Purpose |
|------|---------|
| `AudienceDiscoveryTool` | Discover available audience signals from sellers via UCP |
| `AudienceMatchingTool` | Match campaign audiences to inventory capabilities |
| `CoverageEstimationTool` | Estimate audience coverage for targeting combinations |

### Audience Planning Flow

```
Campaign Brief → Audience Planner Agent → UCP Discovery → Coverage Estimates → Budget Allocation
                        │
                        ├─ Discover seller capabilities via UCP
                        ├─ Match audience requirements to inventory
                        ├─ Estimate coverage per channel
                        └─ Identify gaps and alternatives
```

### Example: Audience-Aware Campaign

```python
# Campaign brief with audience targeting
campaign_brief = {
    "name": "Q1 Brand Campaign",
    "budget": 100000,
    "objectives": ["brand_awareness", "reach"],
    "target_audience": {
        "demographics": {"age": "25-54", "gender": "all"},
        "interests": ["technology", "business"],
        "behaviors": ["in-market-auto"],
    },
    "start_date": "2026-02-01",
    "end_date": "2026-03-31",
}

# The flow will:
# 1. Analyze target_audience via Audience Planner Agent
# 2. Discover seller capabilities using UCP
# 3. Estimate coverage: {"branding": 75%, "ctv": 55%, ...}
# 4. Identify gaps: ["behavioral_targeting: coverage limited to 35-45%"]
# 5. Adjust budget allocation based on coverage
```

### UCP Technical Details

| Property | Value |
|----------|-------|
| **Content-Type** | `application/vnd.ucp.embedding+json; v=1` |
| **Embedding Dimensions** | 256-1024 |
| **Similarity Metric** | Cosine (default), Dot Product, L2 |
| **Consent Required** | Yes (IAB TCF v2) |

---

## Installation

### Prerequisites

- Python 3.11 or higher
- An [Anthropic API key](https://console.anthropic.com/) for Claude
- Internet access (connects to IAB Tech Lab's hosted server by default)

> **Note**: You do **not** need to install the IAB agentic-direct server locally. This system connects to their hosted instance by default.

### Step 1: Clone and Install

```bash
git clone https://github.com/mobtownlabs/ad_buyer_system.git
cd ad_buyer_system

# Install the package
pip install -e .

# Or with dev tools (for testing)
pip install -e ".[dev]"
```

### Step 2: Configure Environment

```bash
cp .env.example .env
```

Edit `.env` with your settings:

```bash
# Required: Your Anthropic API key
ANTHROPIC_API_KEY=sk-ant-api03-xxxxx

# OpenDirect server (default: live IAB Tech Lab server)
IAB_SERVER_URL=https://agentic-direct-server-hwgrypmndq-uk.a.run.app

# Storage
DATABASE_URL=sqlite:///./ad_buyer.db

# Optional: Redis for caching
# REDIS_URL=redis://localhost:6379/0
```

### Step 3: Verify Installation

```bash
# Test the CLI
ad-buyer --help

# Test connection to IAB server
python scripts/test_unified_client.py
```

---

## Quick Start Examples

### Example 1: List Available Products (MCP Protocol)

```python
import asyncio
from ad_buyer.clients import UnifiedClient, Protocol

async def main():
    async with UnifiedClient(protocol=Protocol.MCP) as client:
        # List available products from seller
        result = await client.list_products()
        if result.success:
            for product in result.data[:5]:
                print(f"- {product.get('name')}: ${product.get('rate', 'N/A')} CPM")

asyncio.run(main())
```

Output:
```
- Premium Display: $15.00 CPM
- CTV Streaming: $35.00 CPM
- Mobile App Interstitial: $12.00 CPM
```

### Example 2: Natural Language Search (A2A Protocol)

```python
import asyncio
from ad_buyer.clients import UnifiedClient, Protocol

async def main():
    async with UnifiedClient(protocol=Protocol.A2A) as client:
        # Ask in natural language
        result = await client.send_natural_language(
            "Find CTV inventory with household targeting under $30 CPM"
        )
        print(f"Response: {result.data}")

asyncio.run(main())
```

Output:
```
Response: I found 3 CTV products matching your criteria:
1. Streaming Plus - $28 CPM, household + demographic targeting
2. Connected TV Basic - $25 CPM, household targeting
3. OTT Premium - $29 CPM, household + interest targeting
```

### Example 3: Book a Deal

```python
import asyncio
from ad_buyer.clients import UnifiedClient, Protocol

async def main():
    async with UnifiedClient(protocol=Protocol.MCP) as client:
        # Create an account
        account = await client.create_account(name="Acme Corp")
        account_id = account.data["id"]
        print(f"Created account: {account_id}")

        # Create an order
        order = await client.create_order(
            account_id=account_id,
            name="Q1 2026 Brand Campaign",
            budget=50000
        )
        order_id = order.data["id"]
        print(f"Created order: {order_id}")

        # Create a line item
        line = await client.create_line(
            order_id=order_id,
            product_id="ctv-premium",
            name="CTV Streaming Buy",
            quantity=1_500_000  # 1.5M impressions
        )
        print(f"Created line: {line.data['id']}")
        print(f"Total cost: ${line.data.get('cost', 'TBD')}")

asyncio.run(main())
```

Output:
```
Created account: acc-12345
Created order: ord-67890
Created line: line-11111
Total cost: $45,000
```

### Example 4: Switch Between Protocols

```python
import asyncio
from ad_buyer.clients import UnifiedClient, Protocol

async def main():
    async with UnifiedClient(protocol=Protocol.MCP) as client:
        # Connect to both protocols
        await client.connect_both()

        # Use MCP for fast, deterministic operations
        products = await client.list_products(protocol=Protocol.MCP)
        print(f"Found {len(products.data)} products")

        # Use A2A for natural language recommendations
        recs = await client.send_natural_language(
            "Which of these products would work best for a brand awareness campaign targeting millennials?"
        )
        print(f"Recommendation: {recs.data}")

asyncio.run(main())
```

---

## DSP Use Case: Deal ID Discovery

The Ad Buyer System supports **DSP (Demand Side Platform)** workflows where you obtain Deal IDs from sellers for activation in traditional DSP platforms (The Trade Desk, DV360, Amazon DSP, etc.).

### Identity-Based Tiered Pricing

Sellers offer different pricing based on revealed buyer identity:

| Tier | Identity Required | Discount | Access |
|------|------------------|----------|--------|
| **Public** | None | 0% | Price ranges, limited catalog |
| **Seat** | DSP seat ID | 5% | Fixed prices |
| **Agency** | Agency ID | 10% | Premium inventory, negotiation |
| **Advertiser** | Agency + Advertiser ID | 15% | Volume discounts, full negotiation |

### Example: DSP Deal Discovery

```python
import asyncio
from ad_buyer.clients import UnifiedClient
from ad_buyer.models import BuyerIdentity

async def main():
    # Create buyer identity for best pricing
    identity = BuyerIdentity(
        seat_id="ttd-seat-123",
        agency_id="omnicom-456",
        agency_name="OMD",
        advertiser_id="coca-cola-789",
        advertiser_name="Coca-Cola",
    )

    async with UnifiedClient(buyer_identity=identity) as client:
        # Step 1: Discover inventory with tiered pricing
        inventory = await client.discover_inventory(
            channel="ctv",
            max_cpm=25.0,
        )
        print(f"Found {len(inventory.data)} products")

        # Step 2: Get pricing for a product
        pricing = await client.get_pricing(
            product_id="ctv-premium-001",
            volume=5_000_000,
        )
        print(f"Tiered price: ${pricing.data['pricing']['tiered_price']} CPM")

        # Step 3: Request a Deal ID
        deal = await client.request_deal(
            product_id="ctv-premium-001",
            deal_type="PD",  # Preferred Deal
            impressions=5_000_000,
            flight_start="2026-02-01",
            flight_end="2026-02-28",
        )

        print(f"Deal ID: {deal.data['deal_id']}")
        print(f"Final CPM: ${deal.data['price']}")
        print(f"Activate in TTD: {deal.data['activation_instructions']['ttd']}")

asyncio.run(main())
```

Output:
```
Found 12 products
Tiered price: $17.00 CPM
Deal ID: DEAL-A1B2C3D4
Final CPM: $17.00
Activate in TTD: The Trade Desk > Inventory > Private Marketplace > Add Deal ID: DEAL-A1B2C3D4
```

### Deal Types

| Type | Code | Description |
|------|------|-------------|
| **Programmatic Guaranteed** | `PG` | Fixed price, guaranteed impressions |
| **Preferred Deal** | `PD` | Fixed price, non-guaranteed first-look |
| **Private Auction** | `PA` | Floor price with auction dynamics |

### DSP Tools

The system provides specialized tools for DSP workflows:

- `DiscoverInventoryTool` - Query sellers with identity context
- `GetPricingTool` - Get tier-specific pricing with volume discounts
- `RequestDealTool` - Request Deal IDs with activation instructions

### Run the Example

```bash
cd ad_buyer_system
pip install -e .
python examples/dsp_deal_discovery.py
```

---

## Protocol Options

The system supports multiple protocols for communicating with OpenDirect servers:

| Protocol | Best For | Speed | Flexibility |
|----------|----------|-------|-------------|
| **MCP** | Structured operations (create, update, list) | Fast | Deterministic, 33 tools |
| **A2A** | Natural language queries and discovery | Moderate | Flexible, conversational |
| **UCP** | Audience embedding exchange | Fast | Privacy-preserving matching |

### When to Use Each

- **MCP**: Booking deals, creating orders, listing inventory, automated workflows
- **A2A**: Discovery queries, recommendations, complex questions, conversational interfaces
- **UCP**: Audience planning, coverage estimation, capability discovery

---

## Available MCP Tools

The IAB server provides 33 OpenDirect tools:

| Category | Tools |
|----------|-------|
| **Accounts** | `create_account`, `update_account`, `get_account`, `list_accounts` |
| **Orders** | `create_order`, `update_order`, `get_order`, `list_orders` |
| **Lines** | `create_line`, `update_line`, `get_line`, `list_lines` |
| **Products** | `get_product`, `list_products`, `search_products` |
| **Creatives** | `create_creative`, `update_creative`, `get_creative`, `list_creatives` |
| **Assignments** | `create_assignment`, `delete_assignment`, `get_assignment`, `list_assignments` |
| **Organizations** | `create_organization`, `update_organization`, `get_organization`, `list_organizations` |
| **Change Requests** | `create_changerequest`, `get_changerequest`, `list_changerequests` |
| **Messages** | `create_message`, `get_message`, `list_messages` |

---

## REST API

Start the API server:

```bash
python -m ad_buyer.interfaces.api.main
# Server runs at http://localhost:8000
```

### Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/products` | List available products |
| `GET` | `/products/{id}` | Get product details |
| `POST` | `/accounts` | Create an account |
| `POST` | `/orders` | Create an order |
| `POST` | `/lines` | Create a line item |
| `POST` | `/search` | Search inventory |
| `POST` | `/chat` | Natural language query |
| `POST` | `/audience/plan` | Plan audience targeting (UCP) |

### Example: Search Products via API

```bash
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "CTV inventory under $30 CPM",
    "channel": "ctv",
    "max_cpm": 30
  }'
```

Response:
```json
{
  "results": [
    {
      "product_id": "ctv-001",
      "name": "Streaming Plus",
      "cpm": 28.00,
      "targeting": ["household", "demographic"]
    },
    {
      "product_id": "ctv-002",
      "name": "Connected TV Basic",
      "cpm": 25.00,
      "targeting": ["household"]
    }
  ],
  "total": 2
}
```

---

## CLI Commands

```bash
# View help
ad-buyer --help

# Initialize a campaign brief template
ad-buyer init

# Book a campaign from brief
ad-buyer book campaign_brief.json

# Search inventory
ad-buyer search --channel ctv --max-cpm 30

# Start interactive chat mode
ad-buyer chat

# List your orders
ad-buyer orders list
```

---

## Configuration Reference

All settings can be configured via environment variables or `.env` file:

```bash
# ─────────────────────────────────────────────────────────────────
# REQUIRED
# ─────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY=sk-ant-api03-xxxxx

# ─────────────────────────────────────────────────────────────────
# OPENDIRECT SERVER
# ─────────────────────────────────────────────────────────────────
# Live IAB Tech Lab server (default, recommended)
IAB_SERVER_URL=https://agentic-direct-server-hwgrypmndq-uk.a.run.app

# Local server (for development/testing)
# OPENDIRECT_BASE_URL=http://localhost:3000

# ─────────────────────────────────────────────────────────────────
# STORAGE (choose one)
# ─────────────────────────────────────────────────────────────────
# SQLite (default, good for development)
DATABASE_URL=sqlite:///./ad_buyer.db

# Redis (recommended for production)
# REDIS_URL=redis://localhost:6379/0

# ─────────────────────────────────────────────────────────────────
# UCP (User Context Protocol)
# ─────────────────────────────────────────────────────────────────
UCP_ENABLED=true
UCP_EMBEDDING_DIMENSION=512
UCP_SIMILARITY_THRESHOLD=0.5
UCP_CONSENT_REQUIRED=true

# ─────────────────────────────────────────────────────────────────
# LLM CONFIGURATION
# ─────────────────────────────────────────────────────────────────
DEFAULT_LLM_MODEL=anthropic/claude-sonnet-4-5-20250929
MANAGER_LLM_MODEL=anthropic/claude-opus-4-20250514
LLM_TEMPERATURE=0.3
LLM_MAX_TOKENS=4096

# ─────────────────────────────────────────────────────────────────
# ENVIRONMENT
# ─────────────────────────────────────────────────────────────────
ENVIRONMENT=development
LOG_LEVEL=INFO
```

---

## Project Structure

```
ad_buyer_system/
├── examples/              # Runnable example scripts
│   ├── basic_mcp_usage.py
│   ├── natural_language_a2a.py
│   ├── protocol_switching.py
│   ├── dsp_deal_discovery.py
│   └── campaign_brief.json
├── src/ad_buyer/
│   ├── agents/            # CrewAI agents
│   │   ├── level1/        # Portfolio Manager
│   │   ├── level2/        # Channel Specialists (incl. DSP Agent)
│   │   └── level3/        # Functional Agents (incl. Audience Planner)
│   ├── clients/           # API clients
│   │   ├── unified_client.py    # Unified MCP + A2A + DSP methods
│   │   ├── mcp_client.py        # Direct MCP access
│   │   ├── a2a_client.py        # Natural language
│   │   └── ucp_client.py        # UCP embedding exchange
│   ├── crews/             # CrewAI crews
│   ├── flows/             # Workflow orchestration
│   │   ├── deal_booking_flow.py # Campaign booking flow (with audience planning)
│   │   └── dsp_deal_flow.py     # DSP Deal ID discovery flow
│   ├── interfaces/        # User interfaces
│   │   ├── api/           # FastAPI REST server
│   │   └── cli/           # Typer CLI
│   ├── models/            # Pydantic models
│   │   ├── opendirect.py        # OpenDirect entities
│   │   ├── flow_state.py        # Flow state models
│   │   ├── buyer_identity.py    # DSP buyer identity models
│   │   └── ucp.py               # UCP models (embeddings, capabilities)
│   └── tools/             # CrewAI tools
│       ├── research/      # Research tools
│       ├── execution/     # Booking tools
│       ├── reporting/     # Reporting tools
│       ├── dsp/           # DSP tools (discover, pricing, deals)
│       └── audience/      # Audience tools (discovery, matching, coverage)
├── tests/
│   └── unit/              # Unit tests
└── scripts/               # Test and utility scripts
```

---

## Development

### Run Tests

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run all tests
ANTHROPIC_API_KEY=test pytest tests/ -v

# Run with coverage
pytest tests/ --cov=ad_buyer --cov-report=html

# Test against live IAB server
python scripts/test_unified_client.py
python scripts/test_mcp_e2e.py
python scripts/test_a2a_e2e.py
```

### Code Quality

```bash
# Linting
ruff check src/

# Type checking
mypy src/

# Format code
ruff format src/
```

---

## Troubleshooting

### "ANTHROPIC_API_KEY required"

Make sure your `.env` file exists and contains a valid API key:
```bash
echo "ANTHROPIC_API_KEY=sk-ant-api03-xxxxx" > .env
```

### "Connection refused" to IAB server

Verify connectivity to the IAB Tech Lab server:
```bash
curl https://agentic-direct-server-hwgrypmndq-uk.a.run.app/health
```

### MCP tools not working

Ensure you're using `Protocol.MCP` and the client is connected:
```python
async with UnifiedClient(protocol=Protocol.MCP) as client:
    # Client is automatically connected
    result = await client.list_products()
```

### A2A responses are slow

A2A involves an LLM on the server side, so responses take longer than MCP. Use MCP for performance-critical operations.

---

## Related Projects

- [ad-seller-system](https://github.com/mobtownlabs/ad-seller-system) - Publisher/SSP-side agent system

### IAB Tech Lab Resources

- [agentic-direct](https://github.com/InteractiveAdvertisingBureau/agentic-direct) - IAB Tech Lab reference implementation
- [Demo Client](https://agentic-direct-client-hwgrypmndq-uk.a.run.app/) - Hosted web client
- [A2A Server](https://agentic-direct-server-hwgrypmndq-uk.a.run.app/) - Agent-to-Agent protocol endpoint
- [MCP Info](https://agentic-direct-server-hwgrypmndq-uk.a.run.app/mcp/info) - MCP server metadata
- [MCP SSE](https://agentic-direct-server-hwgrypmndq-uk.a.run.app/mcp/sse) - MCP server-sent events endpoint
- [UCP Specification](https://iabtechlab.com/standards/user-context-protocol/) - User Context Protocol documentation

