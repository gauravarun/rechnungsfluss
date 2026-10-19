# S3 document bucket and DynamoDB tables (ADR-0005). Every object lives under
# a <tenant>/ prefix and every item key starts with TENANT#<tenant>#.

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

variable "env" {
  description = "Environment name, e.g. dev."
  type        = string
}

variable "platform_kms_key_arn" {
  description = "KMS key for tables and the bucket default. Tenant objects are written with their tenant key."
  type        = string
}

locals {
  name = "${var.project}-${var.env}"
}

# --- S3: documents ----------------------------------------------------------

resource "aws_s3_bucket" "documents" {
  # checkov:skip=CKV_AWS_18:Access is audited via CloudTrail S3 data events (added with observability in W6), not server access logs.
  # checkov:skip=CKV_AWS_144:Single-region sandbox holding synthetic data; cross-region replication is out of scope (docs/brief.md).
  bucket_prefix = "${local.name}-docs-"
  force_destroy = false
}

resource "aws_s3_bucket_lifecycle_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id

  rule {
    id     = "expire-noncurrent-and-abort-uploads"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 90
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_s3_bucket_public_access_block" "documents" {
  bucket                  = aws_s3_bucket.documents.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "documents" {
  bucket = aws_s3_bucket.documents.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "documents" {
  bucket = aws_s3_bucket.documents.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.platform_kms_key_arn
    }
    bucket_key_enabled = true
  }
}

data "aws_iam_policy_document" "documents_tls_only" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.documents.arn, "${aws_s3_bucket.documents.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "documents" {
  bucket = aws_s3_bucket.documents.id
  policy = data.aws_iam_policy_document.documents_tls_only.json
}

# EventBridge notifications feed the ingest queue (W3).
resource "aws_s3_bucket_notification" "documents" {
  bucket      = aws_s3_bucket.documents.id
  eventbridge = true
}

# --- DynamoDB ---------------------------------------------------------------

resource "aws_dynamodb_table" "main" {
  name         = "${local.name}-main"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }
  attribute {
    name = "gsi1pk"
    type = "S"
  }
  attribute {
    name = "gsi1sk"
    type = "S"
  }

  # Invoices by state per tenant: gsi1pk = TENANT#<t>#STATE#<state>, gsi1sk = invoice id
  global_secondary_index {
    name            = "gsi1"
    projection_type = "ALL"
    key_schema {
      attribute_name = "gsi1pk"
      key_type       = "HASH"
    }
    key_schema {
      attribute_name = "gsi1sk"
      key_type       = "RANGE"
    }
  }

  point_in_time_recovery {
    enabled = true
  }
  server_side_encryption {
    enabled     = true
    kms_key_arn = var.platform_kms_key_arn
  }
  deletion_protection_enabled = true
}

# Append-only: writers get PutItem with attribute_not_exists only (W3 IAM).
resource "aws_dynamodb_table" "audit" {
  name         = "${local.name}-audit"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "seq"

  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "seq"
    type = "N"
  }

  point_in_time_recovery {
    enabled = true
  }
  server_side_encryption {
    enabled     = true
    kms_key_arn = var.platform_kms_key_arn
  }
  deletion_protection_enabled = true
}

# Powertools idempotency store (ADR-0007).
resource "aws_dynamodb_table" "idempotency" {
  name         = "${local.name}-idempotency"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  attribute {
    name = "id"
    type = "S"
  }
  ttl {
    attribute_name = "expiration"
    enabled        = true
  }
  point_in_time_recovery {
    enabled = true
  }
  server_side_encryption {
    enabled     = true
    kms_key_arn = var.platform_kms_key_arn
  }
}

output "documents_bucket" {
  description = "Name of the document bucket."
  value       = aws_s3_bucket.documents.id
}

output "table_names" {
  description = "DynamoDB table names by role."
  value = {
    main        = aws_dynamodb_table.main.name
    audit       = aws_dynamodb_table.audit.name
    idempotency = aws_dynamodb_table.idempotency.name
  }
}

output "bucket_arn" {
  description = "ARN of the document bucket."
  value       = aws_s3_bucket.documents.arn
}

output "table_arns" {
  description = "DynamoDB table ARNs by role."
  value = {
    main        = aws_dynamodb_table.main.arn
    audit       = aws_dynamodb_table.audit.arn
    idempotency = aws_dynamodb_table.idempotency.arn
  }
}
