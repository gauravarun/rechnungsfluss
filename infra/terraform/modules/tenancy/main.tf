# Per-tenant KMS keys (ADR-0006, layer 3). Key policies are tightened to the
# tenant-scoped roles once those roles exist (W3); until then only the account
# root can administer them.

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

variable "tenants" {
  description = "Tenant IDs; each gets its own KMS key."
  type        = set(string)

  validation {
    condition     = alltrue([for t in var.tenants : can(regex("^[a-z][a-z0-9-]{2,31}$", t))])
    error_message = "Tenant IDs must match ^[a-z][a-z0-9-]{2,31}$ (same rule as domain/tenancy.py)."
  }
}

data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "key" {
  # KMS key policy: "*" means "this key", and account-root admin is the AWS default
  # that keeps the key manageable. Tenant-role grants are added in W3.
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

resource "aws_kms_key" "tenant" {
  for_each                = var.tenants
  description             = "${var.project} data key for tenant ${each.key}"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.key.json
  tags                    = { tenant = each.key }
}

resource "aws_kms_alias" "tenant" {
  for_each      = var.tenants
  name          = "alias/${var.project}-${each.key}"
  target_key_id = aws_kms_key.tenant[each.key].key_id
}

output "kms_key_arns" {
  description = "Tenant ID -> KMS key ARN."
  value       = { for t, k in aws_kms_key.tenant : t => k.arn }
}
