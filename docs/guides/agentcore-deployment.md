# AgentCore Deployment

Deploy the buyer agent to Amazon Bedrock AgentCore as a managed runtime. The buyer handles campaign planning and budget allocation — seller interactions (inventory, pricing, deals) are handled by the seller runtime separately.

---

## Prerequisites

- **AWS CLI** configured with credentials (`aws configure` or `--profile`)
- **Python 3.12+** with `pip install bedrock-agentcore`
- **No Docker required** — CodeBuild builds ARM64 containers in the cloud

---

## Quick Start

```bash
bash infra/aws/agentcore/deploy.sh \
  --mode http \
  --name my-buyer-agent \
  --profile my-aws-profile \
  --test
```

---

## Architecture

The buyer runtime wraps `DealBookingFlow` in a `BedrockAgentCoreApp` container:

```
┌─────────────────────────────────────────────┐
│           AgentCore Container                │
│                                              │
│  ┌────────────────────────────────────────┐ │
│  │  BedrockAgentCoreApp (port 8080)       │ │
│  │  http_main.py                          │ │
│  │                                        │ │
│  │  ┌─────────┐    ┌──────────────────┐  │ │
│  │  │  crew   │    │     chat         │  │ │
│  │  │  mode   │    │     mode         │  │ │
│  │  └────┬────┘    └────────┬─────────┘  │ │
│  │       │                  │            │ │
│  │       ▼                  ▼            │ │
│  │  DealBookingFlow    ChatInterface     │ │
│  │       │                               │ │
│  │       ▼                               │ │
│  │  PortfolioCrew (Bedrock LLM)          │ │
│  │       │                               │ │
│  │       ▼                               │ │
│  │  Channel Specialists                  │ │
│  │  (CTV, Display, Mobile, Performance)  │ │
│  └────────────────────────────────────────┘ │
└─────────────────────────────────────────────┘
```

### Crew Mode (Default)

Runs `DealBookingFlow` with the inner `PortfolioCrew` using Bedrock Converse. The crew allocates budget across channels based on the campaign brief.

```bash
curl -X POST http://localhost:8080/invocations \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Plan a $500K Q4 automotive campaign across CTV and digital video"}'
```

Returns structured JSON with budget allocations, audience coverage, and approval status.

### Chat Mode

Falls back to the existing `ChatInterface` keyword router.

---

## Deploy Script

```bash
bash infra/aws/agentcore/deploy.sh [OPTIONS]

Options:
  --mode http           Runtime mode (HTTP only for buyer)
  --name NAME           Agent name
  --profile PROFILE     AWS CLI profile
  --region REGION       AWS region (default: us-west-2)
  --test                Run integration tests after deploy
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ROUTING_MODE` | `crew` | Default routing mode |
| `DEFAULT_LLM_MODEL` | `bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0` | Bedrock model for PortfolioCrew |
| `DATABASE_URL` | `sqlite:///:memory:` | Database connection string |

---

## Campaign Brief Parsing

The buyer accepts natural language prompts and extracts structured campaign parameters:

| Parameter | Extraction | Example |
|-----------|-----------|---------|
| Budget | `$500K` → 500000, `$2M` → 2000000 | `"Plan a $500K campaign"` |
| Dates | `Q4` → Oct-Dec 2026 | `"Q4 automotive campaign"` |
| Audience | `targeting ...` clause | `"targeting adults 25-54"` |

The `PortfolioCrew` inside `DealBookingFlow` does the actual planning intelligence — budget allocation, channel research, audience coverage estimation.

---

## Testing

```bash
# Unit tests (52 tests)
pytest tests/unit/agentcore/ -v

# Integration tests (3 tests, requires deployed runtime)
pytest tests/integration/agentcore/test_runtime.py \
  --profile genai --agent-name my-buyer-agent -v
```

---

## Bedrock Converse Patch

Same patch as the seller — `patches/crewai_bedrock_fix.py` fixes orphaned toolUse/toolResult blocks and tool argument extraction in CrewAI's Bedrock provider.
