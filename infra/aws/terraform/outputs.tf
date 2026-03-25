output "vpc_id" {
  description = "ID of the VPC"
  value       = module.network.vpc_id
}

output "alb_dns_name" {
  description = "DNS name of the Application Load Balancer"
  value       = module.compute.alb_dns_name
}

output "alb_zone_id" {
  description = "Route53 zone ID of the ALB (for alias records)"
  value       = module.compute.alb_zone_id
}

output "ecr_repository_url" {
  description = "URL of the ECR repository"
  value       = module.compute.ecr_repository_url
}

output "ecs_cluster_name" {
  description = "Name of the ECS cluster"
  value       = module.compute.ecs_cluster_name
}

output "ecs_service_name" {
  description = "Name of the ECS service"
  value       = module.compute.ecs_service_name
}

output "cloudwatch_log_group" {
  description = "CloudWatch log group for the ECS tasks"
  value       = module.compute.cloudwatch_log_group
}

output "redis_endpoint" {
  description = "ElastiCache Redis primary endpoint"
  value       = module.storage.redis_endpoint
}

output "redis_port" {
  description = "ElastiCache Redis port"
  value       = module.storage.redis_port
}
