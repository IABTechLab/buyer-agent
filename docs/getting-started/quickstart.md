# Quickstart

Get the buyer agent running locally, verify it works, and optionally connect to a seller agent for a full end-to-end booking.

## Prerequisites

- Python 3.11 or later
- pip

## Installation

Clone the repository and install dependencies:

```bash
cd ad_buyer_system
pip install -e ".[dev]"
```

For production (no dev/test extras):

```bash
pip install -e .
```

## Configuration

Create a `.env` file in the project root with your LLM API key:

```dotenv
# Minimum — just set your LLM provider key
ANTHROPIC_API_KEY=sk-ant-...
```

??? note "Full configuration options"
    ```dotenv
    # LLM — set the API key for your chosen provider
    ANTHROPIC_API_KEY=sk-ant-...              # For Anthropic (default)
    # OPENAI_API_KEY=sk-xxxxx                 # For OpenAI / Azure
    # COHERE_API_KEY=xxxxx                    # For Cohere

    # Inbound API key for this service (leave empty to disable auth in dev)
    API_KEY=

    # OpenDirect seller connection
    OPENDIRECT_BASE_URL=http://localhost:3000/api/v2.1
    OPENDIRECT_TOKEN=              # OAuth bearer token (optional)
    OPENDIRECT_API_KEY=            # API key for seller (optional)

    # Multi-seller mode (comma-separated URLs)
    SELLER_ENDPOINTS=

    # LLM model overrides — uses litellm provider/model format (any provider works)
    DEFAULT_LLM_MODEL=anthropic/claude-sonnet-4-5-20250929
    MANAGER_LLM_MODEL=anthropic/claude-opus-4-20250514
    # DEFAULT_LLM_MODEL=openai/gpt-4o         # OpenAI example
    # DEFAULT_LLM_MODEL=ollama/llama3          # Local Ollama example

    # Environment
    ENVIRONMENT=development
    LOG_LEVEL=INFO
    ```

All settings are loaded from environment variables or the `.env` file via `pydantic-settings`.

!!! info "LLM Provider Flexibility"
    The buyer agent uses [litellm](https://docs.litellm.ai/) under the hood, supporting **100+ LLM providers** --- OpenAI, Azure, Cohere, Ollama, Vertex AI, Bedrock, and more. Set `DEFAULT_LLM_MODEL` and `MANAGER_LLM_MODEL` using `provider/model-name` format and provide the matching API key environment variable. Agent prompts are tuned for Claude but work with any capable model. See the [litellm provider docs](https://docs.litellm.ai/docs/providers) for the full list.

## Run the Server

```bash
uvicorn ad_buyer.interfaces.api.main:app --reload --port 8001
```

The API will be available at `http://localhost:8001`.

## Verify It Works

```bash
curl http://localhost:8001/health
```

Expected response:

```json
{"status": "healthy", "version": "1.0.0"}
```

## Browse the API Docs

The buyer agent serves interactive API documentation:

- **Swagger UI**: [http://localhost:8001/docs](http://localhost:8001/docs)
- **ReDoc**: [http://localhost:8001/redoc](http://localhost:8001/redoc)

## First API Calls

These calls work without a seller agent running.

### List Bookings

```bash
curl http://localhost:8001/bookings
```

On a fresh server, this returns an empty list:

```json
{"jobs": [], "total": 0}
```

---

## Run with a Seller Agent

The buyer agent connects to seller agents to discover inventory and book deals. This section walks through a full booking workflow with both agents running together.

### Prerequisites

- Buyer agent installed and running (steps above)
- Seller agent installed and running --- follow the [Seller Agent Quickstart](https://iabtechlab.github.io/seller-agent/getting-started/quickstart/)

!!! tip "Default Ports"
    The seller agent runs on port **3000** by default, the buyer agent on port **8001**. The buyer's `OPENDIRECT_BASE_URL` should point to the seller's API (e.g. `http://localhost:3000/api/v2.1`).

### Start Both Agents

**1. Start the seller agent** (in a separate terminal):

```bash
cd seller_agent
uvicorn seller_agent.api.main:app --reload --port 3000
```

Verify: `curl http://localhost:3000/health`

**2. Start the buyer agent** (in another terminal):

```bash
cd ad_buyer_system
uvicorn ad_buyer.interfaces.api.main:app --reload --port 8001
```

Verify: `curl http://localhost:8001/health`

### Booking Workflow

**1. Create a booking** --- submit a campaign brief to the buyer agent:

```bash
curl -X POST http://localhost:8001/bookings \
  -H "Content-Type: application/json" \
  -d '{
    "brief": {
      "name": "Summer Campaign 2026",
      "objectives": ["brand_awareness", "reach"],
      "budget": 50000,
      "start_date": "2026-07-01",
      "end_date": "2026-08-31",
      "target_audience": {
        "demographics": {"age": "25-54"},
        "interests": ["travel", "outdoor"]
      },
      "kpis": {"target_cpm": 12, "viewability": 70},
      "channels": ["branding", "ctv"]
    },
    "auto_approve": false
  }'
```

Response:

```json
{
  "job_id": "a1b2c3d4-...",
  "status": "pending",
  "message": "Booking workflow started. Use GET /bookings/{job_id} to check status."
}
```

**2. Check status** --- poll until the status reaches `awaiting_approval`:

```bash
curl http://localhost:8001/bookings/a1b2c3d4-...
```

**3. Approve recommendations**:

```bash
# Approve all recommendations
curl -X POST http://localhost:8001/bookings/a1b2c3d4-.../approve-all

# Or approve specific products
curl -X POST http://localhost:8001/bookings/a1b2c3d4-.../approve \
  -H "Content-Type: application/json" \
  -d '{"approved_product_ids": ["prod_001", "prod_003"]}'
```

**4. View results**:

```bash
curl http://localhost:8001/bookings/a1b2c3d4-...
```

The response now includes `booked_lines` with confirmed deal details from the seller.

## Next Steps

- [**Buyer Guide Overview**](../guides/overview.md) --- Orientation on the full buyer workflow and all guide topics
- [**Deal Booking Guide**](../guides/deal-booking.md) --- Detailed explanation of the booking lifecycle and deal states
- [**Configuration**](../guides/configuration.md) --- Deep dive into all configuration options
- [**Negotiation Guide**](../guides/negotiation.md) --- Configure automated price negotiation with seller agents
- [**API Reference**](../api/overview.md) --- Explore every endpoint in detail
