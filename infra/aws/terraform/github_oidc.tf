# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

# GitHub Actions OIDC provider + deploy role.
#
# This allows GitHub Actions to assume an AWS IAM role via OIDC (no static
# long-lived credentials stored in GitHub secrets). After `terraform apply`,
# copy the `github_deploy_role_arn` output into the repo's GitHub secret:
#   Settings → Secrets → Actions → AWS_DEPLOY_ROLE_ARN

variable "github_repo" {
  description = "GitHub repository in owner/repo format (e.g. IABTechLab/buyer-agent)"
  type        = string
  default     = "IABTechLab/buyer-agent"
}

data "aws_iam_openid_connect_provider" "github" {
  count = 1
  url   = "https://token.actions.githubusercontent.com"
}

# Create the OIDC provider only when it doesn't already exist in the account.
resource "aws_iam_openid_connect_provider" "github" {
  count = length(data.aws_iam_openid_connect_provider.github) == 0 ? 1 : 0

  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

locals {
  oidc_provider_arn = (
    length(data.aws_iam_openid_connect_provider.github) > 0
    ? data.aws_iam_openid_connect_provider.github[0].arn
    : aws_iam_openid_connect_provider.github[0].arn
  )
}

data "aws_iam_policy_document" "github_deploy_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # Allow the deploy job on main or any workflow_dispatch from the repo.
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo}:*"]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  name               = "ad-buyer-system-github-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_deploy_assume.json
  description        = "Assumed by GitHub Actions via OIDC to deploy buyer-agent to ECS"

  tags = {
    Project   = "ad-buyer-system"
    ManagedBy = "terraform"
  }
}

data "aws_iam_policy_document" "github_deploy_permissions" {
  # ECR — push images
  statement {
    effect = "Allow"
    actions = [
      "ecr:GetAuthorizationToken",
    ]
    resources = ["*"]
  }

  statement {
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [module.compute.ecr_repository_arn]
  }

  # ECS — trigger rolling deploy + wait for stability
  statement {
    effect = "Allow"
    actions = [
      "ecs:UpdateService",
      "ecs:DescribeServices",
      "ecs:DescribeTasks",
      "ecs:DescribeTaskDefinition",
      "ecs:ListTasks",
    ]
    resources = ["*"]
    condition {
      test     = "ArnLike"
      variable = "ecs:cluster"
      values   = [module.compute.ecs_cluster_arn]
    }
  }
}

resource "aws_iam_role_policy" "github_deploy" {
  name   = "ad-buyer-system-github-deploy-policy"
  role   = aws_iam_role.github_deploy.id
  policy = data.aws_iam_policy_document.github_deploy_permissions.json
}

output "github_deploy_role_arn" {
  description = "ARN to set as AWS_DEPLOY_ROLE_ARN in GitHub Actions secrets"
  value       = aws_iam_role.github_deploy.arn
}
