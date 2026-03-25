terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    bucket         = "ad-buyer-system-terraform-state"
    key            = "terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "ad-buyer-system-terraform-lock"
    encrypt        = true
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = "ad-buyer-system"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

locals {
  name_prefix = "ad-buyer-${var.environment}"
}

# -----------------------------------------------------------------------------
# Network module: VPC, subnets, NAT, security groups
# -----------------------------------------------------------------------------
module "network" {
  source = "./modules/network"

  environment = var.environment
  vpc_cidr    = var.vpc_cidr
  project     = "ad-buyer-system"
}

# -----------------------------------------------------------------------------
# Storage module: ElastiCache Redis
# -----------------------------------------------------------------------------
module "storage" {
  source = "./modules/storage"

  name_prefix         = local.name_prefix
  environment         = var.environment
  private_subnet_ids  = module.network.private_subnet_ids
  redis_security_group_id = module.network.redis_security_group_id
  redis_node_type     = var.redis_node_type
}

# -----------------------------------------------------------------------------
# Compute module: ECS Fargate, ALB, EFS, IAM, CloudWatch
# -----------------------------------------------------------------------------
module "compute" {
  source = "./modules/compute"

  environment       = var.environment
  project           = "ad-buyer-system"
  region            = var.region
  container_image_tag = var.container_image_tag
  certificate_arn   = var.certificate_arn

  vpc_id              = module.network.vpc_id
  public_subnet_ids   = module.network.public_subnet_ids
  private_subnet_ids  = module.network.private_subnet_ids
  alb_security_group_id = module.network.alb_security_group_id
  ecs_security_group_id = module.network.ecs_security_group_id
  efs_security_group_id = module.network.efs_security_group_id
  redis_endpoint      = module.storage.redis_endpoint
  redis_port          = module.storage.redis_port
}
