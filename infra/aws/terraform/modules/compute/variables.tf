variable "environment" {
  description = "Deployment environment"
  type        = string
}

variable "project" {
  description = "Project name used for resource naming and tags"
  type        = string
}

variable "region" {
  description = "AWS region"
  type        = string
}

variable "container_image_tag" {
  description = "Docker image tag for the buyer agent container"
  type        = string
}

variable "certificate_arn" {
  description = "ACM certificate ARN for HTTPS. Empty string disables HTTPS."
  type        = string
  default     = ""
}

variable "vpc_id" {
  description = "ID of the VPC"
  type        = string
}

variable "public_subnet_ids" {
  description = "List of public subnet IDs for the ALB"
  type        = list(string)
}

variable "private_subnet_ids" {
  description = "List of private subnet IDs for ECS tasks and EFS"
  type        = list(string)
}

variable "alb_security_group_id" {
  description = "Security group ID for the ALB"
  type        = string
}

variable "ecs_security_group_id" {
  description = "Security group ID for ECS tasks"
  type        = string
}

variable "efs_security_group_id" {
  description = "Security group ID for EFS mount targets"
  type        = string
}

variable "redis_endpoint" {
  description = "ElastiCache Redis primary endpoint address"
  type        = string
}

variable "redis_port" {
  description = "ElastiCache Redis port"
  type        = number
  default     = 6379
}
