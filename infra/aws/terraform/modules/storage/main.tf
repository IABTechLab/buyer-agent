# =============================================================================
# Storage module: ElastiCache Redis for the buyer system
# =============================================================================
# The buyer system uses Redis as its primary production key-value store
# for campaign state, negotiation sessions, and booking records.
# SQLite on EFS is retained as a fallback/local-dev option.
# =============================================================================

# -----------------------------------------------------------------------------
# ElastiCache Redis
# -----------------------------------------------------------------------------

resource "aws_elasticache_subnet_group" "redis" {
  name        = "${var.name_prefix}-redis"
  description = "Subnet group for Ad Buyer Redis"
  subnet_ids  = var.private_subnet_ids

  tags = {
    Name = "${var.name_prefix}-redis-subnet-group"
  }
}

resource "aws_elasticache_replication_group" "redis" {
  replication_group_id = "${var.name_prefix}-redis"
  description          = "Redis cluster for ${var.name_prefix}"

  engine               = "redis"
  engine_version       = "7.1"
  node_type            = var.redis_node_type
  num_cache_clusters   = 1
  port                 = 6379
  parameter_group_name = "default.redis7"

  subnet_group_name  = aws_elasticache_subnet_group.redis.name
  security_group_ids = [var.redis_security_group_id]

  at_rest_encryption_enabled = true
  transit_encryption_enabled = false
  automatic_failover_enabled = false

  snapshot_retention_limit = var.environment == "prod" ? 7 : 0

  tags = {
    Name = "${var.name_prefix}-redis"
  }
}

resource "aws_ssm_parameter" "redis_endpoint" {
  name        = "/${var.name_prefix}/redis-endpoint"
  description = "ElastiCache Redis primary endpoint"
  type        = "String"
  value       = aws_elasticache_replication_group.redis.primary_endpoint_address

  tags = {
    Name = "${var.name_prefix}-redis-endpoint"
  }
}
