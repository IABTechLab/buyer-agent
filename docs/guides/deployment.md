# Deployment

This guide covers how to run the buyer agent in production. Locally, Docker Compose gets you a containerized instance in seconds. For cloud deployment, both CloudFormation and Terraform templates are provided for AWS ECS Fargate with EFS-backed SQLite persistence. Start with Docker Compose for development and testing; move to AWS when you need a durable, always-on deployment.

---

## Quick Start — Docker Compose

The fastest way to run the buyer agent in a container:

```bash
cd infra/docker
docker compose up
```

This starts:

| Service | Port | Purpose |
|---------|------|---------|
| **app** | 8001 | Buyer agent API |

The SQLite database is stored on a Docker volume (`buyerdata`) for persistence across container restarts.

Verify it's running:

```bash
curl http://localhost:8001/health
```

### Environment Variables

The app container reads from `../../.env` (project root). Key settings:

```bash
DATABASE_URL=sqlite:///./data/ad_buyer.db
ANTHROPIC_API_KEY=sk-ant-...           # Or your chosen LLM provider key
SELLER_ENDPOINTS=http://seller:8000    # Seller agent URL(s)
```

See [Quickstart](../getting-started/quickstart.md) for the full variable reference.

### Rebuilding

```bash
docker compose build --no-cache app
docker compose up -d
```

---

## AWS Deployment

Two IaC options are provided — choose based on your team's preference:

### CloudFormation

Nested stack templates in `infra/aws/cloudformation/`:

```
cloudformation/
├── main.yaml       # Root stack (orchestrates nested stacks)
├── network.yaml    # VPC, subnets, NAT, security groups
└── compute.yaml    # ECS Fargate, ALB, EFS, CloudWatch, IAM
```

The buyer uses **EFS** (Elastic File System) for SQLite persistence, rather than Aurora.

Deploy:

```bash
# Upload nested templates to S3
aws s3 sync infra/aws/cloudformation/ s3://your-bucket/cf-templates/

# Create the stack
aws cloudformation create-stack \
  --stack-name ad-buyer-prod \
  --template-url https://your-bucket.s3.amazonaws.com/cf-templates/main.yaml \
  --parameters \
    ParameterKey=Environment,ParameterValue=production \
    ParameterKey=AnthropicApiKeySSMParam,ParameterValue=/ad-buyer/anthropic-api-key \
    ParameterKey=ContainerImage,ParameterValue=123456789.dkr.ecr.us-east-1.amazonaws.com/ad-buyer:latest \
  --capabilities CAPABILITY_NAMED_IAM
```

### Terraform

Modular Terraform in `infra/aws/terraform/`:

```
terraform/
├── main.tf
├── variables.tf
├── outputs.tf
├── terraform.tfvars.example
└── modules/
    ├── network/
    └── compute/
```

Deploy:

```bash
cd infra/aws/terraform
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your values

terraform init
terraform plan
terraform apply
```

### AWS Architecture

Both options deploy the same architecture:

- **Compute**: ECS Fargate (256 CPU, 512 MB memory)
- **Storage**: EFS for SQLite persistence (mounted at `/app/data`)
- **Networking**: VPC with public/private subnets across 2 AZs
- **Load Balancer**: Application Load Balancer with HTTPS
- **Secrets**: SSM Parameter Store (SecureString)
- **Logging**: CloudWatch Logs

!!! note "Single-Task Deployment"
    The buyer agent uses SQLite, which supports only one concurrent writer.
    AWS deployments run a single ECS task (`desired_count=1`) with EFS-backed
    persistence. For horizontal scaling, a PostgreSQL migration is planned.

---

## Building the Container Image

For ECR deployment:

```bash
# Build
docker build -t ad-buyer -f infra/docker/Dockerfile .

# Tag and push to ECR
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin 123456789.dkr.ecr.us-east-1.amazonaws.com
docker tag ad-buyer:latest 123456789.dkr.ecr.us-east-1.amazonaws.com/ad-buyer:latest
docker push 123456789.dkr.ecr.us-east-1.amazonaws.com/ad-buyer:latest
```

---

## Running with Seller Agent

For end-to-end testing, run both agents:

```bash
# Terminal 1 — Seller agent
cd ../ad_seller_system/infra/docker
docker compose up

# Terminal 2 — Buyer agent (pointing at seller)
cd ../ad_buyer_system
SELLER_ENDPOINTS=http://host.docker.internal:8000 docker compose -f infra/docker/docker-compose.yml up
```

Or use the seller agent's Docker image directly — see the commented `seller` service in `docker-compose.yml`.
