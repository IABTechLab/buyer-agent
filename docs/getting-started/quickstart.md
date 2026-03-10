# Quickstart

Get the buyer agent running locally and execute your first booking workflow.

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
# LLM
ANTHROPIC_API_KEY=sk-ant-...

# Inbound API key for this service (leave empty to disable auth in dev)
API_KEY=

# OpenDirect seller connection
OPENDIRECT_BASE_URL=http://localhost:3000/api/v2.1
OPENDIRECT_TOKEN=              # OAuth bearer token (optional)
OPENDIRECT_API_KEY=            # API key for seller (optional)

# Multi-seller mode (comma-separated URLs)
SELLER_ENDPOINTS=

# LLM model overrides (defaults shown)
DEFAULT_LLM_MODEL=anthropic/claude-sonnet-4-5-20250929
MANAGER_LLM_MODEL=anthropic/claude-opus-4-20250514

# Environment
ENVIRONMENT=development
LOG_LEVEL=INFO
```

All settings are loaded from environment variables or the `.env` file via `pydantic-settings`.

## Run the Server

```bash
uvicorn ad_buyer.interfaces.api.main:app --reload --port 8001
```

The API will be available at `http://localhost:8001`. Interactive docs are served at `/docs` (Swagger) and `/redoc`.

## Verify It Works

```bash
curl http://localhost:8001/health
```

Expected response:

```json
{"status": "healthy", "version": "1.0.0"}
```

## Example Workflow

### 1. Create a booking

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

### 2. Check status

```bash
curl http://localhost:8001/bookings/a1b2c3d4-...
```

Poll until `status` reaches `awaiting_approval` (or `completed` if `auto_approve` was true).

### 3. Approve recommendations

```bash
curl -X POST http://localhost:8001/bookings/a1b2c3d4-.../approve-all
```

Or approve specific products:

```bash
curl -X POST http://localhost:8001/bookings/a1b2c3d4-.../approve \
  -H "Content-Type: application/json" \
  -d '{"approved_product_ids": ["prod_001", "prod_003"]}'
```

### 4. View results

```bash
curl http://localhost:8001/bookings/a1b2c3d4-...
```

The response now includes `booked_lines` with confirmed deal details.
