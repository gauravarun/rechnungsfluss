# Sandbox environment. Applied only by the account owner (see docs/runbook.md);
# CI runs fmt/validate/tflint/checkov without credentials.

terraform {
  required_version = ">= 1.9"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.67"
    }
  }
  # Partial config: bucket/key/region supplied via -backend-config at init.
  backend "s3" {
    use_lockfile = true
    encrypt      = true
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      project    = var.project
      env        = var.env
      managed_by = "terraform"
      data_class = "synthetic"
    }
  }
}

variable "region" {
  description = "AWS region."
  type        = string
  default     = "eu-central-1"
}

variable "project" {
  description = "Project name used in resource names."
  type        = string
  default     = "rechnungsfluss"
}

variable "env" {
  description = "Environment name."
  type        = string
  default     = "dev"
}

variable "tenants" {
  description = "Tenant IDs."
  type        = set(string)
  default     = ["nordwerk-de", "nordwerk-at"]
}

data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "platform_key" {
  # checkov:skip=CKV_AWS_109:Key policy (resource is this key only), AWS default admin statement.
  # checkov:skip=CKV_AWS_111:Key policy (resource is this key only), AWS default admin statement.
  # checkov:skip=CKV_AWS_356:Key policy (resource is this key only), AWS default admin statement.
  statement {
    sid       = "AccountAdmin"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }
  statement {
    sid       = "CloudWatchLogsForOwnLogGroups"
    actions   = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:Describe*"]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["logs.${var.region}.amazonaws.com"]
    }
    condition {
      test     = "ArnLike"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values   = ["arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:*"]
    }
  }
  statement {
    sid       = "EventBridgeToEncryptedIngestQueue"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_kms_key" "platform" {
  description         = "${var.project}-${var.env} platform key (tables, bucket default)"
  enable_key_rotation = true
  policy              = data.aws_iam_policy_document.platform_key.json
}

module "tenancy" {
  source  = "../../modules/tenancy"
  project = var.project
  tenants = var.tenants
}

module "storage" {
  source               = "../../modules/storage"
  project              = var.project
  env                  = var.env
  platform_kms_key_arn = aws_kms_key.platform.arn
}

variable "lambda_package_path" {
  description = "Lambda zip from scripts/build_lambda.sh."
  type        = string
  default     = "../../../../dist/lambda.zip"
}

module "pipeline" {
  source               = "../../modules/pipeline"
  project              = var.project
  env                  = var.env
  region               = var.region
  tenants              = var.tenants
  bucket_name          = module.storage.documents_bucket
  bucket_arn           = module.storage.bucket_arn
  table_arns           = module.storage.table_arns
  table_names          = module.storage.table_names
  platform_kms_key_arn = aws_kms_key.platform.arn
  tenant_kms_key_arns  = module.tenancy.kms_key_arns
  lambda_package_path  = var.lambda_package_path
}

output "state_machine_arn" {
  description = "Pipeline state machine ARN."
  value       = module.pipeline.state_machine_arn
}

output "documents_bucket" {
  description = "Name of the document bucket."
  value       = module.storage.documents_bucket
}

output "table_names" {
  description = "DynamoDB table names by role."
  value       = module.storage.table_names
}

output "tenant_kms_key_arns" {
  description = "Tenant ID -> KMS key ARN."
  value       = module.tenancy.kms_key_arns
}
