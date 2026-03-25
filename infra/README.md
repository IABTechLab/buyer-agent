# Ad Buyer System - Infrastructure

Infrastructure-as-Code templates and deployment configuration for the
ad buyer system.

## Architecture Overview

```
                 Internet
                    |
              [ALB (HTTPS)]
                    |
         [ECS Fargate Service]
           /        |        \
     [Redis]     [EFS]    [CloudWatch]
   (ElastiCache) (SQLite    (Logs)
                  fallback)
```

**Key components:**

- **ECS/Fargate**: Runs the buyer FastAPI application as serverless containers
- **ElastiCache Redis**: Primary production key-value store for campaign
  state, negotiation sessions, and booking records
- **EFS**: Mounted for SQLite fallback persistence
- **ALB**: Internet-facing Application Load Balancer with HTTPS termination
- **VPC**: Isolated network with public/private subnets across 2 AZs
- **CloudWatch**: Centralized logging

## Directory Structure

```
infra/
  aws/
    cloudformation/     # CloudFormation nested stacks
      main.yaml         # Root stack (orchestrates all)
      network.yaml      # VPC, subnets, security groups
      storage.yaml      # ElastiCache Redis
      compute.yaml      # ECS, ALB, IAM, EFS, logging
    terraform/          # Terraform modules (alternative to CFn)
      main.tf           # Root configuration
      variables.tf      # Input variables
      outputs.tf        # Stack outputs
      terraform.tfvars.example
      modules/
        network/        # VPC, subnets, security groups
        storage/        # ElastiCache Redis
        compute/        # ECS, ALB, IAM, EFS, logging
  docker/
    Dockerfile          # Multi-stage production image
    docker-compose.yml  # Local dev stack (app + Redis)
    .dockerignore
```

## Prerequisites

- AWS CLI v2 configured with appropriate credentials
- Terraform >= 1.5.0 (for Terraform deployment)
- Docker (for local development and image building)
- An ACM certificate (for HTTPS) or leave empty for HTTP-only

## Secrets Setup

Before deploying, create the following SSM SecureString parameters:

```bash
# Anthropic API key (used by the buyer agent)
aws ssm put-parameter \
  --name "/ad-buyer-system/anthropic-api-key" \
  --type SecureString \
  --value "sk-ant-..."
```

## Deployment Options

### Option 1: Terraform (Recommended)

```bash
cd infra/aws/terraform

# Copy and configure variables
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your values

# Initialize
terraform init

# Plan
terraform plan

# Apply
terraform apply
```

### Option 2: CloudFormation

1. Upload nested templates to S3:
```bash
BUCKET=your-cfn-templates-bucket
aws s3 sync infra/aws/cloudformation/ \
  s3://$BUCKET/ad-buyer-system/cloudformation/
```

2. Deploy the root stack:
```bash
aws cloudformation create-stack \
  --stack-name ad-buyer-production \
  --template-url https://$BUCKET.s3.amazonaws.com/ad-buyer-system/cloudformation/main.yaml \
  --parameters \
    ParameterKey=Environment,ParameterValue=production \
    ParameterKey=TemplatesBucketName,ParameterValue=$BUCKET \
    ParameterKey=ContainerImage,ParameterValue=YOUR_ECR_IMAGE_URI \
    ParameterKey=CertificateArn,ParameterValue=YOUR_ACM_CERT_ARN \
  --capabilities CAPABILITY_NAMED_IAM
```

### Option 3: Local Development with Docker Compose

```bash
cd infra/docker
docker compose up        # Start app + Redis
docker compose down -v   # Stop and clean up
```

This starts the buyer application on port 8001 with a local Redis instance.

## CI/CD Pipeline

The repository includes two GitHub Actions workflows:

- **`.github/workflows/ci.yml`**: Runs on every push and PR to main.
  Executes linting (ruff), tests (with Redis service), and Docker build
  verification.

- **`.github/workflows/deploy.yml`**: Runs on push to main (non-docs
  changes) or manual trigger. Builds the Docker image, pushes to ECR,
  and updates the ECS service.

### Required GitHub Secrets

| Secret | Description |
|--------|-------------|
| `AWS_DEPLOY_ROLE_ARN` | IAM role ARN for OIDC-based deployment |

### Required GitHub Environments

Create `staging` and `production` environments in your repository
settings. The `production` environment should require manual approval.

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `STORAGE_TYPE` | Storage backend: `redis`, `sqlite`, or `hybrid` | `redis` in prod |
| `DATABASE_URL` | SQLite connection string | `sqlite:///./data/ad_buyer.db` |
| `REDIS_URL` | Redis connection string | Set by IaC |
| `ANTHROPIC_API_KEY` | Anthropic API key (injected from SSM) | - |
| `ENVIRONMENT` | Deployment environment name | Set by IaC |

## Cost Estimates (us-east-1)

Approximate monthly costs for a minimal staging deployment:

| Resource | Type | Est. Cost |
|----------|------|-----------|
| NAT Gateway | 1x | ~$32 |
| ALB | 1x | ~$16 |
| ECS Fargate | 0.25 vCPU / 512 MiB | ~$9 |
| ElastiCache Redis | cache.t3.micro | ~$12 |
| EFS | Pay-per-use | ~$1 |
| CloudWatch Logs | Pay-per-use | ~$1 |
| **Total** | | **~$71/mo** |

Production deployments with larger instance types and multi-AZ Redis
will be higher.
