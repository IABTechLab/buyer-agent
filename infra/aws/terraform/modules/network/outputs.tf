output "vpc_id" {
  description = "ID of the VPC"
  value       = aws_vpc.this.id
}

output "public_subnet_ids" {
  description = "IDs of the public subnets"
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "IDs of the private subnets"
  value       = aws_subnet.private[*].id
}

output "alb_security_group_id" {
  description = "Security group ID for the ALB"
  value       = aws_security_group.alb.id
}

output "ecs_security_group_id" {
  description = "Security group ID for ECS tasks"
  value       = aws_security_group.ecs.id
}

output "efs_security_group_id" {
  description = "Security group ID for EFS mount targets"
  value       = aws_security_group.efs.id
}

output "redis_security_group_id" {
  description = "Security group ID for Redis"
  value       = aws_security_group.redis.id
}
