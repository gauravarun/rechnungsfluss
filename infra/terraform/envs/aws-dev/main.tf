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
