###############################################################################
# Data sources
###############################################################################
data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, 2)

  tags = {
    Project     = var.project
    Environment = var.environment
  }
}

###############################################################################
# VPC
###############################################################################
resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(local.tags, {
    Name = "${var.project}-${var.environment}-vpc"
  })
}

###############################################################################
# Internet Gateway
###############################################################################
resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = merge(local.tags, {
    Name = "${var.project}-${var.environment}-igw"
  })
}

###############################################################################
# Public Subnets
###############################################################################
resource "aws_subnet" "public" {
  count = length(local.azs)

  vpc_id                  = aws_vpc.this.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = true

  tags = merge(local.tags, {
    Name = "${var.project}-${var.environment}-public-${local.azs[count.index]}"
    Tier = "public"
  })
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  tags = merge(local.tags, {
    Name = "${var.project}-${var.environment}-public-rt"
  })
}

resource "aws_route" "public_internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.this.id
}

resource "aws_route_table_association" "public" {
  count = length(aws_subnet.public)

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

###############################################################################
# NAT Gateway (single, in first public subnet)
###############################################################################
resource "aws_eip" "nat" {
  domain = "vpc"

  tags = merge(local.tags, {
    Name = "${var.project}-${var.environment}-nat-eip"
  })
}

resource "aws_nat_gateway" "this" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id

  tags = merge(local.tags, {
    Name = "${var.project}-${var.environment}-nat"
  })

  depends_on = [aws_internet_gateway.this]
}

###############################################################################
# Private Subnets
###############################################################################
resource "aws_subnet" "private" {
  count = length(local.azs)

  vpc_id            = aws_vpc.this.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 100)
  availability_zone = local.azs[count.index]

  tags = merge(local.tags, {
    Name = "${var.project}-${var.environment}-private-${local.azs[count.index]}"
    Tier = "private"
  })
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.this.id

  tags = merge(local.tags, {
    Name = "${var.project}-${var.environment}-private-rt"
  })
}

resource "aws_route" "private_nat" {
  route_table_id         = aws_route_table.private.id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.this.id
}

resource "aws_route_table_association" "private" {
  count = length(aws_subnet.private)

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

###############################################################################
# Security Groups
###############################################################################

# ALB Security Group — allows inbound HTTP/HTTPS from the internet
resource "aws_security_group" "alb" {
  name        = "${var.project}-${var.environment}-alb-sg"
  description = "Allow HTTP/HTTPS inbound to ALB"
  vpc_id      = aws_vpc.this.id

  tags = merge(local.tags, {
    Name = "${var.project}-${var.environment}-alb-sg"
  })
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTP from internet"
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTPS from internet"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "alb_all" {
  security_group_id = aws_security_group.alb.id
  description       = "Allow all outbound"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

# ECS Security Group — allows inbound from ALB only
resource "aws_security_group" "ecs" {
  name        = "${var.project}-${var.environment}-ecs-sg"
  description = "Allow inbound from ALB to ECS tasks"
  vpc_id      = aws_vpc.this.id

  tags = merge(local.tags, {
    Name = "${var.project}-${var.environment}-ecs-sg"
  })
}

resource "aws_vpc_security_group_ingress_rule" "ecs_from_alb" {
  security_group_id            = aws_security_group.ecs.id
  description                  = "App port from ALB"
  from_port                    = 8001
  to_port                      = 8001
  ip_protocol                  = "tcp"
  referenced_security_group_id = aws_security_group.alb.id
}

resource "aws_vpc_security_group_egress_rule" "ecs_all" {
  security_group_id = aws_security_group.ecs.id
  description       = "Allow all outbound"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

# EFS Security Group — allows NFS from ECS tasks
resource "aws_security_group" "efs" {
  name        = "${var.project}-${var.environment}-efs-sg"
  description = "Allow NFS inbound from ECS tasks"
  vpc_id      = aws_vpc.this.id

  tags = merge(local.tags, {
    Name = "${var.project}-${var.environment}-efs-sg"
  })
}

resource "aws_vpc_security_group_ingress_rule" "efs_from_ecs" {
  security_group_id            = aws_security_group.efs.id
  description                  = "NFS from ECS tasks"
  from_port                    = 2049
  to_port                      = 2049
  ip_protocol                  = "tcp"
  referenced_security_group_id = aws_security_group.ecs.id
}

resource "aws_vpc_security_group_egress_rule" "efs_all" {
  security_group_id = aws_security_group.efs.id
  description       = "Allow all outbound"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

# Redis Security Group — allows inbound from ECS on Redis port
resource "aws_security_group" "redis" {
  name        = "${var.project}-${var.environment}-redis-sg"
  description = "Allow Redis inbound from ECS tasks"
  vpc_id      = aws_vpc.this.id

  tags = merge(local.tags, {
    Name = "${var.project}-${var.environment}-redis-sg"
  })
}

resource "aws_vpc_security_group_ingress_rule" "redis_from_ecs" {
  security_group_id            = aws_security_group.redis.id
  description                  = "Redis from ECS tasks"
  from_port                    = 6379
  to_port                      = 6379
  ip_protocol                  = "tcp"
  referenced_security_group_id = aws_security_group.ecs.id
}

resource "aws_vpc_security_group_egress_rule" "redis_all" {
  security_group_id = aws_security_group.redis.id
  description       = "Allow all outbound"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}
