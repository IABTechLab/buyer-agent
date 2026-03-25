# Developer Setup Guide

Set up the buyer agent infrastructure, connect to seller agents and SSPs, and generate credentials for your media buying team to use in Claude Desktop.

## Prerequisites

- Python 3.13
- Docker (for deployment)
- Seller agent URLs (at least one seller must be reachable)
- SSP API keys (optional: PubMatic, Magnite, Index Exchange)
- Anthropic API key

## Step 1: Deploy the Buyer Agent

```bash
# Clone and install
git clone https://github.com/IABTechLab/buyer-agent.git
cd buyer-agent
pip install -e .

# Or with Docker
cd infra/docker
docker compose up
```

## Step 2: Configure Environment

Create a `.env` file:

```env
# Required
ANTHROPIC_API_KEY=sk-ant-...

# Buyer Identity
IAB_SERVER_URL=http://localhost:8001

# Storage (SQLite for dev, Postgres for prod)
DATABASE_URL=sqlite:///./ad_buyer.db

# Environment
ENVIRONMENT=development
LOG_LEVEL=INFO
```

## Step 3: Connect to Sellers

Set the `SELLER_ENDPOINTS` variable to a comma-separated list of seller agent MCP URLs:

```env
# Single seller
SELLER_ENDPOINTS=http://localhost:3000

# Multiple sellers
SELLER_ENDPOINTS=http://espn.example.com,http://conde.example.com,http://nytimes.example.com
```

Each URL should point to a running seller agent. The buyer will use these endpoints for inventory discovery, deal negotiation, and order management.

### Verify seller connectivity

After starting the buyer agent, run:

```bash
curl http://localhost:8001/health
```

You should see all configured seller endpoints listed under `seller_connections`.

## Step 4: Configure SSP Connectors (Optional)

If your media buyers need to import deals from SSPs directly, configure the connector credentials:

```env
# OpenDirect legacy mode (if using a single OpenDirect server)
OPENDIRECT_BASE_URL=http://localhost:3000/api/v2.1
OPENDIRECT_API_KEY=your-opendirect-key
```

The buyer agent's SSP connector tools (`import_from_pubmatic`, `import_from_magnite`, `import_from_index_exchange`) can be called by the business team once credentials are in place.

## Step 5: Configure Optional Services

```env
# Redis (for event bus — optional, falls back to in-memory)
REDIS_URL=redis://localhost:6379

# CORS (if browser clients need access)
CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:8080

# LLM model overrides (optional)
DEFAULT_LLM_MODEL=anthropic/claude-sonnet-4-5-20250929
MANAGER_LLM_MODEL=anthropic/claude-opus-4-20250514
```

## Step 6: Start the Server

```bash
uvicorn ad_buyer.interfaces.api.main:app --host 0.0.0.0 --port 8001
```

Verify: `curl http://localhost:8001/health`

## Step 7: Generate Operator Credentials

Create an inbound API key for your business team. Set `API_KEY` in your `.env`:

```env
# Inbound API key — business team will use this in Claude Desktop
# Leave empty to disable auth (development only)
API_KEY=sk-operator-XXXXX
```

> Generate a strong random key: `python -c "import secrets; print('sk-operator-' + secrets.token_urlsafe(32))"`

Restart the server after setting `API_KEY`. All incoming MCP requests (from Claude Desktop, ChatGPT, Cursor, etc.) will now require this key.

## Step 8: Hand Off

Give your media buying team:

1. **MCP URL**: `http://your-server:8001/mcp/sse` (or your public URL)
2. **API key**: the value you set in `API_KEY`

They'll connect Claude Desktop using the [Claude Desktop Setup Guide](../claude-desktop-setup.md) and complete the business configuration (deal templates, approval thresholds, seller API keys) through the interactive setup wizard.

## Verify the Full Setup

```bash
# Health check
curl http://localhost:8001/health

# Setup status (shows what's configured and what's missing)
curl http://localhost:8001/api/v1/setup/status

# MCP tools list (requires running SSE client — use Claude Desktop or curl with SSE)
curl -N http://localhost:8001/mcp/sse
```

Expected health response:

```json
{
  "status": "healthy",
  "version": "1.0.0",
  "database": "healthy",
  "seller_connections": "configured (2 endpoints)",
  "event_bus": "healthy"
}
```
