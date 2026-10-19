# GitHub Actions OIDC role for the nightly live LLM eval (ADR-0010).
# No long-lived keys: the eval job exchanges its GitHub OIDC token for this role,
# which may only invoke the configured Claude models on Bedrock.
#
# Status: statically checked; not yet applied. The exact IAM actions used by the
# Bedrock Messages ("Mantle") endpoint are verified in runbook §4 step 4 (smoke test).

terraform {
  required_version = ">= 1.9"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.67"
    }
  }
}

variable "project" {
  description = "Project name used in resource names."
  type        = string
}

variable "github_repository" {
  description = "owner/name of the repository allowed to assume the role."
  type        = string
}

variable "region" {
  description = "Bedrock region."
  type        = string
}

variable "model_ids" {
  description = "Bedrock model IDs the eval may invoke (primary and refusal fallback)."
  type        = list(string)
  default     = ["anthropic.claude-opus-5-5", "anthropic.claude-opus-4-8"]
}

variable "create_oidc_provider" {
  description = "Set false if the account already has the GitHub OIDC provider."
  type        = bool
  default     = true
}

data "aws_caller_identity" "current" {}

resource "aws_iam_openid_connect_provider" "github" {
  count          = var.create_oidc_provider ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

locals {
  oidc_provider_arn = var.create_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : "arn:aws:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/token.actions.githubusercontent.com"
}

data "aws_iam_policy_document" "trust" {
  statement {
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
    # Only workflows running on main (nightly schedule or manual dispatch), never PRs or forks.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repository}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "eval" {
  name                 = "${var.project}-ci-llm-eval"
  assume_role_policy   = data.aws_iam_policy_document.trust.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "invoke" {
  statement {
    sid     = "InvokeConfiguredClaudeModelsOnly"
    actions = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = concat(
      [for m in var.model_ids : "arn:aws:bedrock:${var.region}::foundation-model/${m}"],
      [for m in var.model_ids : "arn:aws:bedrock:*:${data.aws_caller_identity.current.account_id}:inference-profile/*.${m}"],
    )
  }
}

resource "aws_iam_role_policy" "invoke" {
  name   = "invoke-claude"
  role   = aws_iam_role.eval.id
  policy = data.aws_iam_policy_document.invoke.json
}

output "role_arn" {
  description = "Set as the repository variable AWS_EVAL_ROLE_ARN."
  value       = aws_iam_role.eval.arn
}
