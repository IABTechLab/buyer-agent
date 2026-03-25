# Deployment & Operations Guide

This guide covers everything needed to run the buyer agent in any environment — from a local development setup to a production AWS deployment — and how to operate and troubleshoot it once it is running.

---

## Table of Contents

1. [Local Development Setup](#local-development-setup)
2. [Docker Deployment](#docker-deployment)
3. [AWS Deployment](#aws-deployment)
4. [Environment Variables & Configuration](#environment-variables--configuration)
5. [Health Checks & Monitoring](#health-checks--monitoring)
6. [MCP Server Setup & Connectivity](#mcp-server-setup--connectivity)
7. [Backup & Recovery](#backup--recovery)
8. [Troubleshooting](#troubleshooting)

---

## Local Development Setup

### Prerequisites

- Python 3.11 or later (3.12 recommended)
- `pip` or `uv`
- An LLM API key (Anthropic, OpenAI, or any litellm-supported provider)
- Git

### Install Dependencies

```bash
git clone https://github.com/IABTechLab/buyer-agent.git
cd buyer-agent

# Create and activate a virtual environment
python -m venv venv
source venv/bin/activate   # macOS/Linux
# .\venv\Scripts\activate  # Windows

# Install with development extras
pip install -e ".[dev]"
```

For production (no test or linting extras):

```bash
pip install -e .
```

### Configure Environment

Copy the example environment file and set your credentials:

```bash
cp .env.example .env
```

Minimum required settings:

```dotenv
ANTHROPIC_API_KEY=sk-ant-...
```

Full development configuration:

```dotenv
# LLM provider (Anthropic default; see litellm docs for others)
ANTHROPIC_API_KEY=sk-ant-...

# Inbound API key for this service (leave empty to disable auth in dev)
API_KEY=

# Seller connection
SELLER_ENDPOINTS=http://localhost:8000
IAB_SERVER_URL=http://localhost:8000

# LLM models
DEFAULT_LLM_MODEL=anthropic/claude-sonnet-4-5-20250929
MANAGER_LLM_MODEL=anthropic/claude-opus-4-20250514

# Storage
DATABASE_URL=sqlite:///./ad_buyer.db

# Logging
ENVIRONMENT=development
LOG_LEVEL=INFO
```

See the [Configuration Reference](#environment-variables--configuration) below for the full variable list.

### Run the Development Server

```bash
uvicorn ad_buyer.interfaces.api.main:app --reload --port 8001
```

The API is available at `http://localhost:8001`.

Interactive API docs:

- Swagger UI: `http://localhost:8001/docs`
- ReDoc: `http://localhost:8001/redoc`

### Verify the Installation

```bash
curl http://localhost:8001/health
# Expected: {"status": "healthy", "version": "1.0.0"}
```

### Running Tests

```bash
ANTHROPIC_API_KEY=test pytest tests/ -v
```

Run with coverage:

```bash
pytest tests/ -v --cov=src/ad_buyer --cov-report=term-missing
```

Lint:

```bash
ruff check src/
```

---

## Docker Deployment

### Quick Start

The fastest way to run the buyer agent in a container:

```bash
cd infra/docker
docker compose up
```

This starts a single container:

| Service | Port | Purpose |
|---------|------|---------|
| **app** | 8001 | Buyer agent API |

The SQLite database is persisted on a Docker volume (`buyerdata`) and survives container restarts.

Verify it is running:

```bash
curl http://localhost:8001/health
```

### Environment Variables in Docker

The Docker Compose file reads from `../../.env` (the project root) via `env_file`. Set at minimum:

```dotenv
ANTHROPIC_API_KEY=sk-ant-...
```

The `DATABASE_URL` is overridden inside `docker-compose.yml` to point at the container-local path:

```yaml
DATABASE_URL: sqlite:///./data/ad_buyer.db
```

Do not change this unless you are mounting a different volume path.

### Starting in Background Mode

```bash
docker compose up -d
```

Follow logs:

```bash
docker compose logs -f app
```

### Rebuilding the Image

```bash
docker compose build --no-cache app
docker compose up -d
```

### Stopping and Cleaning Up

```bash
# Stop without removing data
docker compose down

# Stop and remove volumes (destroys the SQLite database)
docker compose down -v
```

### Running with a Seller Agent

For end-to-end testing, run both agents simultaneously:

```bash
# Terminal 1 — Seller agent
cd ../ad_seller_system/infra/docker
docker compose up

# Terminal 2 — Buyer agent (pointing at the seller)
cd ../ad_buyer_system
SELLER_ENDPOINTS=http://host.docker.internal:8000 \
  docker compose -f infra/docker/docker-compose.yml up
```

Or uncomment the `seller` service block in `infra/docker/docker-compose.yml` to run both from a single compose file.

### Building the Image for ECR

For AWS ECR deployment, build and push manually:

```bash
# Build from the project root
docker build -t ad-buyer -f infra/docker/Dockerfile .

# Authenticate with ECR
aws ecr get-login-password --region us-east-1 \
  | docker login --username AWS --password-stdin \
      123456789.dkr.ecr.us-east-1.amazonaws.com

# Tag and push
docker tag ad-buyer:latest \
  123456789.dkr.ecr.us-east-1.amazonaws.com/ad-buyer:latest
docker push \
  123456789.dkr.ecr.us-east-1.amazonaws.com/ad-buyer:latest
```

Replace `123456789` with your AWS account ID and adjust the region as needed.

---

## AWS Deployment

The buyer agent runs on **ECS Fargate** with **EFS-backed SQLite persistence**. Two IaC options are provided; both deploy the same architecture.

### Architecture Overview

| Component | AWS Service | Notes |
|-----------|------------|-------|
| Compute | ECS Fargate | 256 CPU / 512 MB, single task |
| Storage | EFS (Elastic File System) | SQLite file mounted at `/app/data` |
| Networking | VPC, public + private subnets | 2 AZs |
| Load balancer | Application Load Balancer | HTTPS with ACM cert, HTTP → HTTPS redirect |
| Secrets | SSM Parameter Store (SecureString) | Anthropic API key |
| Logging | CloudWatch Logs | 30-day retention by default |

!!! warning "Single-task constraint"
    SQLite supports only one concurrent writer. AWS deployments must run exactly **one ECS task** (`DesiredCount: 1`). Running multiple tasks will corrupt the database. A PostgreSQL migration is planned for horizontal scaling.

### Prerequisites

- AWS CLI configured with appropriate permissions
- An ACM certificate ARN for your domain (or use the HTTP listener only during evaluation)
- A container image pushed to ECR (see [Building the Image for ECR](#building-the-image-for-ecr) above)
- An S3 bucket for CloudFormation template storage (CloudFormation option only)

### Option A: CloudFormation

Template layout:

```
infra/aws/cloudformation/
├── main.yaml       # Root stack — orchestrates nested stacks
├── network.yaml    # VPC, subnets, NAT gateway, security groups
└── compute.yaml    # ECS Fargate, ALB, EFS, CloudWatch, IAM roles
```

**Step 1 — Store your Anthropic API key in SSM:**

```bash
aws ssm put-parameter \
  --name /ad-buyer-system/anthropic-api-key \
  --value "sk-ant-..." \
  --type SecureString \
  --region us-east-1
```

**Step 2 — Upload nested templates to S3:**

```bash
aws s3 sync infra/aws/cloudformation/ \
  s3://your-bucket/ad-buyer-system/cloudformation/
```

**Step 3 — Deploy the stack:**

```bash
aws cloudformation create-stack \
  --stack-name ad-buyer-prod \
  --template-url https://your-bucket.s3.amazonaws.com/ad-buyer-system/cloudformation/main.yaml \
  --parameters \
    ParameterKey=Environment,ParameterValue=production \
    ParameterKey=TemplatesBucketName,ParameterValue=your-bucket \
    ParameterKey=ContainerImage,ParameterValue=123456789.dkr.ecr.us-east-1.amazonaws.com/ad-buyer:latest \
    ParameterKey=CertificateArn,ParameterValue=arn:aws:acm:us-east-1:123456789:certificate/... \
    ParameterKey=AnthropicApiKeyParameter,ParameterValue=/ad-buyer-system/anthropic-api-key \
  --capabilities CAPABILITY_NAMED_IAM \
  --region us-east-1
```

**Monitor the deployment:**

```bash
aws cloudformation describe-stack-events \
  --stack-name ad-buyer-prod \
  --region us-east-1 \
  --query 'StackEvents[*].[LogicalResourceId,ResourceStatus,ResourceStatusReason]' \
  --output table
```

**Update the stack after a new image push:**

```bash
aws cloudformation update-stack \
  --stack-name ad-buyer-prod \
  --use-previous-template \
  --parameters \
    ParameterKey=ContainerImage,ParameterValue=123456789.dkr.ecr.us-east-1.amazonaws.com/ad-buyer:v1.2.0 \
    ParameterKey=Environment,UsePreviousValue=true \
    ParameterKey=TemplatesBucketName,UsePreviousValue=true \
    ParameterKey=CertificateArn,UsePreviousValue=true \
    ParameterKey=AnthropicApiKeyParameter,UsePreviousValue=true \
  --capabilities CAPABILITY_NAMED_IAM \
  --region us-east-1
```

### Option B: Terraform

Module layout:

```
infra/aws/terraform/
├── main.tf
├── variables.tf
├── outputs.tf
├── terraform.tfvars.example
└── modules/
    ├── network/
    └── compute/
```

**Step 1 — Initialize Terraform:**

```bash
cd infra/aws/terraform
terraform init
```

Terraform uses an S3 backend for state:

```hcl
backend "s3" {
  bucket         = "ad-buyer-system-terraform-state"
  key            = "terraform.tfstate"
  region         = "us-east-1"
  dynamodb_table = "ad-buyer-system-terraform-lock"
  encrypt        = true
}
```

Create the S3 bucket and DynamoDB table before running `terraform init` for the first time.

**Step 2 — Configure variables:**

```bash
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars`:

```hcl
environment         = "prod"
region              = "us-east-1"
vpc_cidr            = "10.0.0.0/16"
container_image_tag = "latest"
certificate_arn     = "arn:aws:acm:us-east-1:123456789:certificate/..."
```

**Step 3 — Plan and apply:**

```bash
terraform plan
terraform apply
```

**Key outputs after apply:**

```bash
terraform output alb_dns_name       # Point your DNS CNAME here
terraform output ecs_cluster_name
terraform output ecs_service_name
terraform output cloudwatch_log_group
```

**Deploy a new image:**

```bash
terraform apply -var="container_image_tag=v1.2.0"
```

---

## Environment Variables & Configuration

All settings are loaded from environment variables or a `.env` file via `pydantic-settings`. Shell environment variables take precedence over `.env` values.

### API Keys

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | Yes | `""` | Anthropic API key for Claude models. Required for all agent functionality. |
| `API_KEY` | No | `""` | Inbound API key for authenticating requests to this service. When empty, authentication is disabled (development mode). Set a value in production. |

Authentication is enforced via the `X-API-Key` header. Public paths (`/health`, `/docs`, `/openapi.json`, `/redoc`) are always unauthenticated.

### Seller Connectivity

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SELLER_ENDPOINTS` | No | `""` | Comma-separated list of seller MCP/A2A server URLs. Used by the `UnifiedClient` for multi-seller workflows. |
| `IAB_SERVER_URL` | No | `http://localhost:8001` | Primary seller server URL. Used as the default `base_url` for single-seller flows and protocol clients. |
| `OPENDIRECT_BASE_URL` | No | `http://localhost:3000/api/v2.1` | Base URL for the OpenDirect 2.1 REST API (legacy single-seller mode). |
| `OPENDIRECT_TOKEN` | No | `None` | Bearer token for OpenDirect authentication. |
| `OPENDIRECT_API_KEY` | No | `None` | API key for OpenDirect authentication. |

### LLM Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DEFAULT_LLM_MODEL` | `anthropic/claude-sonnet-4-5-20250929` | Model for Level 2 channel specialists and Level 3 functional agents. |
| `MANAGER_LLM_MODEL` | `anthropic/claude-opus-4-20250514` | Model for the Level 1 Portfolio Manager. Opus is used for strategic reasoning. |
| `LLM_TEMPERATURE` | `0.3` | Default temperature. Individual agents use tuned values (0.1–0.5). |
| `LLM_MAX_TOKENS` | `4096` | Maximum token output per LLM call. |

Models use [litellm](https://docs.litellm.ai/) format — `provider/model-name`. Any litellm-supported provider works (OpenAI, Azure, Cohere, Ollama, Vertex AI, Bedrock, etc.):

```dotenv
DEFAULT_LLM_MODEL=openai/gpt-4o
MANAGER_LLM_MODEL=anthropic/claude-opus-4-20250514
```

### Database

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite:///./ad_buyer.db` | SQLAlchemy connection string for deal storage. SQLite for development; PostgreSQL for production multi-instance deployments. |

```dotenv
# SQLite (default, development)
DATABASE_URL=sqlite:///./ad_buyer.db

# PostgreSQL (production, when horizontal scaling is needed)
DATABASE_URL=postgresql://buyer:pass@db.example.com:5432/ad_buyer
```

### Agent Behavior

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | `None` | Redis URL for CrewAI memory persistence and session caching. When unset, in-memory storage is used. |
| `CREW_MEMORY_ENABLED` | `True` | Enable CrewAI agent memory across tasks. |
| `CREW_VERBOSE` | `True` | Enable verbose CrewAI logging. Set to `False` in production. |
| `CREW_MAX_ITERATIONS` | `15` | Maximum iterations per crew task before forced completion. |

### CORS & Environment

| Variable | Default | Description |
|----------|---------|-------------|
| `CORS_ALLOWED_ORIGINS` | `http://localhost:3000,http://localhost:8080` | Comma-separated list of allowed CORS origins. |
| `ENVIRONMENT` | `development` | Runtime environment identifier (`development`, `staging`, `production`). |
| `LOG_LEVEL` | `INFO` | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. |

### Example Configurations

**Minimal local development:**

```dotenv
ANTHROPIC_API_KEY=sk-ant-...
```

**Full production:**

```dotenv
ANTHROPIC_API_KEY=sk-ant-...
API_KEY=your-service-api-key

SELLER_ENDPOINTS=https://seller1.example.com,https://seller2.example.com
IAB_SERVER_URL=https://primary-seller.example.com

DATABASE_URL=postgresql://buyer:pass@db.example.com:5432/ad_buyer
REDIS_URL=redis://cache.example.com:6379/0

CREW_VERBOSE=False
CREW_MAX_ITERATIONS=10

CORS_ALLOWED_ORIGINS=https://dashboard.example.com
ENVIRONMENT=production
LOG_LEVEL=WARNING
```

**Testing:**

```dotenv
ANTHROPIC_API_KEY=test-key
API_KEY=test-api-key
DATABASE_URL=sqlite:///./test_ad_buyer.db
ENVIRONMENT=testing
LOG_LEVEL=DEBUG
CREW_VERBOSE=True
CREW_MAX_ITERATIONS=5
```

### AWS Secrets Management

In AWS deployments, the Anthropic API key is stored in SSM Parameter Store as a SecureString and injected into the container as an environment variable at runtime. The ECS task execution role is granted `ssm:GetParameter` on the specific parameter ARN.

To add additional secrets (seller API keys, service credentials):

1. Store in SSM: `aws ssm put-parameter --name /ad-buyer-system/my-secret --value "..." --type SecureString`
2. Add the parameter ARN to the `SSMParameterAccess` IAM policy in `compute.yaml` or the Terraform compute module
3. Add a `Secrets` entry to the container definition referencing the parameter ARN

---

## Health Checks & Monitoring

### Health Endpoint

The `/health` endpoint is always unauthenticated and returns immediately:

```bash
curl http://localhost:8001/health
# {"status": "healthy", "version": "1.0.0"}
```

This endpoint is used by:

- Docker HEALTHCHECK (30-second interval, 5-second timeout, 3 retries)
- ALB target group health check (30-second interval, 10-second timeout, 2 healthy / 3 unhealthy threshold)
- ECS task health check (30-second interval, 5-second timeout, 60-second start period)

A non-200 response or timeout causes the container to be replaced automatically.

### Checking Job Status

Monitor active booking jobs:

```bash
# List all jobs
curl http://localhost:8001/bookings

# Filter by status
curl "http://localhost:8001/bookings?status=running"

# Get a specific job
curl http://localhost:8001/bookings/<job-id>
```

Job status values:

| Status | Description |
|--------|-------------|
| `pending` | Job created, flow not yet started |
| `running` | Flow is executing (budget allocation, research) |
| `awaiting_approval` | Recommendations ready for human review |
| `completed` | All deals booked successfully |
| `failed` | Flow encountered an unrecoverable error |

### Event Bus Monitoring

The event bus provides structured observability across all flows. Query recent events:

```bash
# All recent events
curl "http://localhost:8001/events?limit=50"

# Events for a specific flow
curl "http://localhost:8001/events?flow_id=<flow-id>"

# Events by type (e.g., pacing alerts)
curl "http://localhost:8001/events?event_type=pacing.deviation_detected"
```

Key event types to monitor:

| Event Type | Significance |
|-----------|-------------|
| `pacing.deviation_detected` | Campaign is over/underpacing — may need intervention |
| `pacing.reallocation_recommended` | Budget reallocation proposal generated |
| `booking.failed` | Deal booking failed — check `errors` on the job |
| `negotiation.completed` | Price negotiation finished |

### CloudWatch Logging (AWS)

All application logs are sent to CloudWatch. The log group is:

```
/ecs/{environment}/ad-buyer
```

Retrieve recent logs:

```bash
aws logs tail /ecs/production/ad-buyer --follow --region us-east-1
```

Filter for errors:

```bash
aws logs filter-log-events \
  --log-group-name /ecs/production/ad-buyer \
  --filter-pattern "ERROR" \
  --region us-east-1
```

### Budget Pacing Monitoring

The pacing engine generates snapshots that capture campaign delivery health. Use the event bus to watch for deviation alerts:

```bash
# Check for critical pacing alerts
curl "http://localhost:8001/events?event_type=pacing.deviation_detected"
```

A `deviation_detected` event with `alert_level: critical` means the campaign is more than 25% off expected pace and may need manual intervention.

Pacing alert levels:

| Direction | Warning (>10% deviation) | Critical (>25% deviation) |
|-----------|--------------------------|---------------------------|
| Underpacing | Monitor; may self-correct | Investigate delivery issues |
| Overpacing | Monitor budget burn | Pause or reduce bids |

---

## MCP Server Setup & Connectivity

### Overview

The buyer agent exposes its own MCP server for external clients (Claude Desktop, Cursor, Windsurf, custom agents). The MCP server is mounted automatically on the FastAPI app at startup and exposes buyer operations as structured tools.

MCP endpoint:

```
http://localhost:8001/mcp/sse
```

Available tool categories:

| Category | Tools |
|----------|-------|
| Foundation | `get_setup_status`, `health_check`, `get_config` |
| Campaign Management | `list_campaigns`, `get_campaign_status`, `check_pacing`, `review_budgets` |

### Connecting Claude Desktop

Add the buyer agent to your Claude Desktop MCP configuration (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "ad-buyer-agent": {
      "command": "npx",
      "args": [
        "mcp-remote",
        "http://localhost:8001/mcp/sse"
      ]
    }
  }
}
```

Restart Claude Desktop after editing the configuration. The buyer agent tools will appear in the Claude tool panel.

### Connecting Other MCP Clients

Any client supporting Streamable HTTP (SSE) transport can connect:

```python
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async with streamablehttp_client("http://localhost:8001/mcp/sse") as (read, write, _):
    async with ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        result = await session.call_tool("health_check", {})
```

### Buyer-to-Seller MCP Connectivity

The buyer agent acts as an **MCP client** to seller agents (in addition to exposing its own MCP server). Configure seller connectivity via:

```dotenv
SELLER_ENDPOINTS=http://seller1.example.com:8000,http://seller2.example.com:8000
```

The buyer's `UnifiedClient` connects to the seller's MCP SSE endpoint at `{base_url}/mcp/sse`. Protocol selection is automatic — MCP for structured tool calls, A2A for discovery and negotiation.

**Test seller MCP connectivity manually:**

```bash
# List available seller tools
curl http://seller.example.com:8000/mcp/tools

# Or call a tool directly (SimpleMCP fallback)
curl -X POST http://seller.example.com:8000/mcp/call \
  -H "Content-Type: application/json" \
  -d '{"name": "list_products", "arguments": {}}'
```

### MCP on AWS

In production AWS deployments, MCP connectivity between buyer and seller agents is typically over private VPC networking. If both are running in the same VPC:

- Seller URL uses the ECS service discovery DNS name or ALB internal endpoint
- No public internet required for agent-to-agent communication

If connecting to an external seller:

- Ensure the ECS task's security group allows outbound HTTPS (port 443)
- Use the seller's ALB DNS name as the `SELLER_ENDPOINTS` value

---

## Backup & Recovery

### What Needs to Be Backed Up

The buyer agent has two types of state:

| State | Location | Durability |
|-------|---------|-----------|
| **Active jobs** | In-memory (`jobs` dict) | Lost on restart; dual-written to SQLite |
| **Deal store** | SQLite (`ad_buyer.db`) | Durable on disk; on EFS in AWS |
| **Pacing snapshots** | SQLite (`ad_buyer.db`) | Same as deal store |

The in-memory `jobs` dict is the primary job store. SQLite is a best-effort duplicate for recovery after restarts. On restart, completed and failed jobs are readable from SQLite; running jobs that were interrupted will show stale `running` status in memory and must be resubmitted.

### Local Backup

Back up the SQLite database file directly:

```bash
# Simple copy
cp ad_buyer.db ad_buyer_backup_$(date +%Y%m%d).db

# Or use SQLite's online backup (safe with live connections)
sqlite3 ad_buyer.db ".backup 'ad_buyer_backup.db'"
```

Schedule periodic backups with cron:

```bash
# /etc/cron.d/ad-buyer-backup
0 2 * * * buyer sqlite3 /data/ad_buyer.db ".backup '/backups/ad_buyer_$(date +\%Y\%m\%d).db'"
```

### AWS Backup

#### EFS Backup

AWS Backup automatically backs up EFS volumes. Enable it in the CloudFormation/Terraform templates or manually:

```bash
aws backup start-backup-job \
  --backup-vault-name ad-buyer-backup-vault \
  --resource-arn arn:aws:elasticfilesystem:us-east-1:123456789:file-system/fs-xxxxxx \
  --iam-role-arn arn:aws:iam::123456789:role/AWSBackupDefaultServiceRole \
  --region us-east-1
```

#### Recommended Backup Policy

| Frequency | Retention | Storage Class |
|-----------|-----------|--------------|
| Daily | 30 days | EFS Standard |
| Weekly | 12 weeks | EFS Standard-IA |
| Monthly | 12 months | EFS Standard-IA |

#### Recovery from EFS Backup

To restore from an AWS Backup recovery point:

1. In the AWS Console, go to AWS Backup → Recovery Points
2. Select the recovery point for the EFS file system
3. Choose **Restore** — this creates a new EFS volume from the point-in-time backup
4. Update the ECS task definition to mount the restored EFS volume
5. Deploy the updated task definition

#### Manual EFS Export (Point-in-Time)

To export the database file from a running ECS task:

```bash
# Use ECS Execute Command (enabled in the CloudFormation/Terraform templates)
aws ecs execute-command \
  --cluster production-ad-buyer-cluster \
  --task <task-id> \
  --container ad-buyer \
  --interactive \
  --command "/bin/sh"

# Inside the container:
cp /app/data/ad_buyer.db /tmp/ad_buyer_export.db
```

Then use `aws s3 cp` from within the container, or pipe the file through the session.

### Database Migration

When upgrading the buyer agent to a new version that adds schema changes:

1. Back up the current database before deploying
2. Deploy the new container image
3. The `DealStore` and `PacingStore` use `CREATE TABLE IF NOT EXISTS` — new tables are added automatically
4. Existing rows are preserved; new columns require manual `ALTER TABLE` migrations if the schema changes

---

## Troubleshooting

### Server Will Not Start

**Symptom:** `uvicorn` fails to start, or the container exits immediately.

**Check 1 — Missing dependencies:**

```bash
pip install -e ".[dev]"
# Verify the package is installed
python -c "import ad_buyer; print('OK')"
```

**Check 2 — Port already in use:**

```bash
lsof -i :8001
# If something is already on port 8001, stop it or change the port:
uvicorn ad_buyer.interfaces.api.main:app --reload --port 8002
```

**Check 3 — Invalid environment variables:**

The settings module validates on startup. Look for `ValidationError` in the output:

```bash
python -c "from ad_buyer.config.settings import settings; print(settings)"
```

---

### Health Check Returns 503 / Container Keeps Restarting

**Symptom:** `curl http://localhost:8001/health` returns 503, or ECS tasks fail health checks and restart repeatedly.

**Check 1 — Application logs:**

```bash
# Docker
docker compose logs app

# AWS CloudWatch
aws logs tail /ecs/production/ad-buyer --follow --region us-east-1
```

**Check 2 — Start period too short (AWS):**

The ECS health check has a 60-second `startPeriod`. If the application takes longer to start (e.g., during a cold start with heavy dependency loading), increase the `StartPeriod` in `compute.yaml`:

```yaml
HealthCheck:
  StartPeriod: 120
```

**Check 3 — EFS mount failure (AWS):**

EFS mount issues cause the task to fail before the app starts. Check ECS task stopped reason:

```bash
aws ecs describe-tasks \
  --cluster production-ad-buyer-cluster \
  --tasks <task-id> \
  --region us-east-1 \
  --query 'tasks[*].stoppedReason'
```

Common cause: EFS mount targets are not yet available in the subnets. Wait a few minutes after EFS creation before deploying tasks.

---

### Job Stuck in "running" Status

**Symptom:** A booking job shows `status: running` indefinitely and never transitions.

**Cause:** The background task failed silently or the process was interrupted.

**Resolution:**

1. Check the job's error list: `curl http://localhost:8001/bookings/<job-id>`
2. If `errors` is empty but the job is stuck, the process was likely killed mid-flow (e.g., container restart)
3. The job cannot automatically recover. Resubmit the campaign brief as a new booking
4. In production, implement a watchdog that detects stale `running` jobs and marks them `failed` after a timeout

---

### LLM API Errors

**Symptom:** Jobs fail with errors like `AuthenticationError`, `RateLimitError`, or `APIConnectionError`.

**Check 1 — API key validity:**

```bash
# Test the Anthropic API key directly
curl -H "x-api-key: $ANTHROPIC_API_KEY" \
     -H "anthropic-version: 2023-06-01" \
     https://api.anthropic.com/v1/models | jq '.models[0]'
```

**Check 2 — Rate limits:**

Reduce concurrency by lowering `CREW_MAX_ITERATIONS` and limiting the number of concurrent bookings. CrewAI's multi-agent workflows make many LLM calls in parallel.

**Check 3 — Model availability:**

If a specific model is unavailable, switch to a different model:

```dotenv
DEFAULT_LLM_MODEL=anthropic/claude-haiku-3-5-20241022
MANAGER_LLM_MODEL=anthropic/claude-sonnet-4-5-20250929
```

---

### Seller Connection Failures

**Symptom:** Jobs fail with `ConnectionRefusedError`, `ConnectTimeout`, or seller tools return empty results.

**Check 1 — Seller is running:**

```bash
curl http://localhost:8000/health  # or the seller's configured URL
```

**Check 2 — SELLER_ENDPOINTS configuration:**

```bash
python -c "from ad_buyer.config.settings import settings; print(settings.get_seller_endpoints())"
```

**Check 3 — Network reachability (Docker):**

If the buyer and seller are in separate Docker networks, use `host.docker.internal` as the seller hostname:

```dotenv
SELLER_ENDPOINTS=http://host.docker.internal:8000
```

**Check 4 — MCP SSE endpoint:**

Test the seller's MCP endpoint directly:

```bash
curl -N http://seller.example.com:8000/mcp/sse  # Should stream SSE events
```

---

### Database Errors

**Symptom:** `sqlite3.OperationalError: database is locked` or `disk I/O error`.

**Cause 1 — Multiple writers (SQLite limitation):**

Only one process may write to SQLite at a time. Ensure `DesiredCount: 1` in ECS, and that no other process is accessing the database file concurrently.

**Cause 2 — File permissions (Docker volume):**

The container runs as user `buyer` (UID 1000). The Docker volume or EFS mount must be writable by this user. The EFS access point in `compute.yaml` is pre-configured with `OwnerUid: "1000"`.

**Check database integrity:**

```bash
sqlite3 ad_buyer.db "PRAGMA integrity_check;"
# Should return: ok
```

**Recover a corrupted database:**

```bash
sqlite3 corrupt.db ".recover" | sqlite3 recovered.db
```

---

### MCP Client Cannot Connect to Seller

**Symptom:** `IABMCPClient` raises `ConnectionError` or falls back to `SimpleMCPClient` unexpectedly.

**Check 1 — MCP SDK installed:**

```bash
python -c "import mcp; print(mcp.__version__)"
```

If not installed, install it: `pip install mcp`

**Check 2 — Seller SSE endpoint:**

```bash
# The SSE endpoint should keep the connection open
curl -N -H "Accept: text/event-stream" http://seller.example.com:8000/mcp/sse
```

**Check 3 — Firewall / security groups:**

SSE connections use long-lived HTTP connections. Ensure that any load balancer or proxy has a sufficiently long idle timeout (300+ seconds recommended).

---

### Agent Hierarchy Scaling Considerations

The buyer agent runs a three-level agent hierarchy for campaign bookings:

| Level | Agent | Model | LLM Calls per Run |
|-------|-------|-------|------------------|
| L1 | Portfolio Manager | Opus (manager) | 1–3 |
| L2 | Channel Specialists (×4) | Sonnet (default) | 4–8 each |
| L3 | Functional Agents | Sonnet (default) | 2–5 each |

A full campaign run makes 20–50+ LLM calls. For high-volume environments:

- Monitor LLM API rate limits and request quotas
- Consider separate API keys for the Portfolio Manager (Opus) vs. specialist agents (Sonnet)
- Use `CREW_MAX_ITERATIONS` to cap runaway agent loops
- Set `CREW_VERBOSE=False` in production to reduce log volume

---

## Related

- [Configuration Reference](configuration.md) — Full environment variable documentation
- [Architecture Overview](../architecture/overview.md) — Agent hierarchy and system components
- [Budget Pacing](budget-pacing.md) — Pacing engine and reallocation logic
- [Deal Booking Guide](deal-booking.md) — Booking flow and deal lifecycle
- [Event Bus](../event-bus/overview.md) — Structured observability events
- [Quickstart](../getting-started/quickstart.md) — First-run walkthrough
