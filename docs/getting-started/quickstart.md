# Quickstart

Get the buyer agent running locally and verify it works.

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

Create a `.env` file in the project root:

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
    The buyer agent uses [litellm](https://docs.litellm.ai/) under the hood, supporting **100+ LLM providers** — OpenAI, Azure, Cohere, Ollama, Vertex AI, Bedrock, and more. Set `DEFAULT_LLM_MODEL` and `MANAGER_LLM_MODEL` using `provider/model-name` format and provide the matching API key environment variable. Agent prompts are tuned for Claude but work with any capable model. See the [litellm provider docs](https://docs.litellm.ai/docs/providers) for the full list.

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

## Next Steps

- [**Running with Seller**](running-with-seller.md) — Connect to the seller agent and execute a full booking workflow end-to-end.
- [**Configuration**](../guides/configuration.md) — Deep dive into all configuration options.
- [**Deal Booking Guide**](../guides/deal-booking.md) — Understand the full booking lifecycle.
- [**API Reference**](../api/overview.md) — Explore every endpoint in detail.
