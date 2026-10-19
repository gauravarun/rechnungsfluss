# Ingest queue, Lambdas, Step Functions workflow and tenant-scoped IAM (ADR-0001, -0006, -0007).
#
#   S3 <tenant>/inbox/* --EventBridge--> SQS ingest (+DLQ) --> ingest Lambda --> Step Functions
#
# Status: statically checked (validate, tflint, checkov); not yet applied to a real account.

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
  description = "Environment name."
  type        = string
}

variable "region" {
  description = "AWS region (for kms:ViaService conditions)."
  type        = string
}

variable "tenants" {
  description = "Tenant IDs allowed as the `tenant` session tag."
  type        = set(string)
}

variable "bucket_name" {
  description = "Document bucket name."
  type        = string
}

variable "bucket_arn" {
  description = "Document bucket ARN."
  type        = string
}

variable "table_arns" {
  description = "DynamoDB table ARNs by role: main, audit, idempotency."
  type        = map(string)
}

variable "table_names" {
  description = "DynamoDB table names by role: main, audit, idempotency."
  type        = map(string)
}

variable "platform_kms_key_arn" {
  description = "Platform KMS key (tables, queues, logs, Lambda environment)."
  type        = string
}

variable "tenant_kms_key_arns" {
  description = "Tenant ID -> KMS key ARN for tenant objects in S3."
  type        = map(string)
}

variable "lambda_package_path" {
  description = "Path to the Lambda zip built by scripts/build_lambda.sh."
  type        = string
}

variable "review_timeout_seconds" {
  description = "How long an invoice may wait in review before the execution fails (default 30 days)."
  type        = number
  default     = 2592000
}

variable "log_retention_days" {
  description = "CloudWatch log retention."
  type        = number
  default     = 365
}

locals {
  name      = "${var.project}-${var.env}"
  functions = toset(["ingest", "step", "await-review"])
  handlers = {
    ingest         = "rechnungsfluss.handlers.lambdas.ingest_handler"
    step           = "rechnungsfluss.handlers.lambdas.step_handler"
    "await-review" = "rechnungsfluss.handlers.lambdas.await_review_handler"
  }
  timeouts = { ingest = 60, step = 120, "await-review" = 30 }
}

data "aws_caller_identity" "current" {}

# --- Ingest queue -------------------------------------------------------------------

resource "aws_sqs_queue" "ingest_dlq" {
  name                      = "${local.name}-ingest-dlq"
  message_retention_seconds = 1209600
  kms_master_key_id         = var.platform_kms_key_arn
}

resource "aws_sqs_queue" "ingest" {
  name                       = "${local.name}-ingest"
  visibility_timeout_seconds = 6 * local.timeouts.ingest
  kms_master_key_id          = var.platform_kms_key_arn
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.ingest_dlq.arn
    maxReceiveCount     = 5
  })
}

resource "aws_cloudwatch_event_rule" "document_uploaded" {
  name        = "${local.name}-document-uploaded"
  description = "New document in a tenant inbox"
  event_pattern = jsonencode({
    source        = ["aws.s3"]
    "detail-type" = ["Object Created"]
    detail = {
      bucket = { name = [var.bucket_name] }
      object = { key = [{ wildcard = "*/inbox/*" }] }
    }
  })
}

resource "aws_cloudwatch_event_target" "to_ingest_queue" {
  rule = aws_cloudwatch_event_rule.document_uploaded.name
  arn  = aws_sqs_queue.ingest.arn
}

data "aws_iam_policy_document" "ingest_queue" {
  statement {
    sid       = "EventBridgeRuleOnly"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.ingest.arn]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_cloudwatch_event_rule.document_uploaded.arn]
    }
  }
}

resource "aws_sqs_queue_policy" "ingest" {
  queue_url = aws_sqs_queue.ingest.id
  policy    = data.aws_iam_policy_document.ingest_queue.json
}

# --- Lambda roles (no data access of their own) --------------------------------------

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "fn" {
  for_each           = local.functions
  name               = "${local.name}-${each.key}"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

resource "aws_cloudwatch_log_group" "fn" {
  for_each          = local.functions
  name              = "/aws/lambda/${local.name}-${each.key}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.platform_kms_key_arn
}

data "aws_iam_policy_document" "fn_base" {
  for_each = local.functions
  statement {
    sid       = "OwnLogGroup"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.fn[each.key].arn}:*"]
  }
  statement {
    sid       = "Tracing"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"] # X-Ray does not support resource-level permissions
  }
  statement {
    sid       = "AssumeTenantDataRoleWithKnownTenantTag"
    actions   = ["sts:AssumeRole", "sts:TagSession"]
    resources = [aws_iam_role.tenant_data.arn]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/tenant"
      values   = tolist(var.tenants)
    }
    condition {
      test     = "ForAllValues:StringEquals"
      variable = "aws:TagKeys"
      values   = ["tenant"]
    }
  }
  statement {
    sid       = "EnvironmentDecryption"
    actions   = ["kms:Decrypt"]
    resources = [var.platform_kms_key_arn]
  }
}

resource "aws_iam_role_policy" "fn_base" {
  for_each = local.functions
  name     = "base"
  role     = aws_iam_role.fn[each.key].id
  policy   = data.aws_iam_policy_document.fn_base[each.key].json
}

data "aws_iam_policy_document" "ingest_extra" {
  statement {
    sid       = "ConsumeIngestQueue"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [aws_sqs_queue.ingest.arn]
  }
  statement {
    sid       = "StartPipeline"
    actions   = ["states:StartExecution"]
    resources = [aws_sfn_state_machine.pipeline.arn]
  }
  statement {
    sid       = "QueueAndStateMachineEncryption"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [var.platform_kms_key_arn]
  }
}

resource "aws_iam_role_policy" "ingest_extra" {
  name   = "ingest"
  role   = aws_iam_role.fn["ingest"].id
  policy = data.aws_iam_policy_document.ingest_extra.json
}

# --- Tenant data role: the only role with data access (ADR-0006, layer 2) -------------

data "aws_iam_policy_document" "tenant_data_trust" {
  statement {
    actions = ["sts:AssumeRole", "sts:TagSession"]
    principals {
      type        = "AWS"
      identifiers = [for r in aws_iam_role.fn : r.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/tenant"
      values   = tolist(var.tenants)
    }
  }
}

resource "aws_iam_role" "tenant_data" {
  name                 = "${local.name}-tenant-data"
  assume_role_policy   = data.aws_iam_policy_document.tenant_data_trust.json
  max_session_duration = 3600
}

resource "aws_iam_role_policy" "tenant_data" {
  name = "tenant-scoped-data"
  role = aws_iam_role.tenant_data.id
  policy = templatefile("${path.module}/policies/tenant-data.json.tftpl", {
    main_table_arn           = var.table_arns["main"]
    audit_table_arn          = var.table_arns["audit"]
    idempotency_table_arn    = var.table_arns["idempotency"]
    bucket_arn               = var.bucket_arn
    tenant_kms_key_arns_json = jsonencode(values(var.tenant_kms_key_arns))
    platform_kms_key_arn     = var.platform_kms_key_arn
    region                   = var.region
  })
}

# --- Functions -------------------------------------------------------------------------

resource "aws_lambda_function" "fn" {
  for_each = local.functions
  # checkov:skip=CKV_AWS_117:No VPC resources are accessed; S3, DynamoDB, STS and Step Functions are reached via AWS endpoints.
  # checkov:skip=CKV_AWS_116:Not invoked asynchronously: ingest is an SQS event source (queue has a DLQ), the others are invoked synchronously by Step Functions (retry/catch in the workflow).
  # checkov:skip=CKV_AWS_272:Code signing is out of scope for the sandbox; revisit before any production use (docs/security.md).
  function_name                  = "${local.name}-${each.key}"
  role                           = aws_iam_role.fn[each.key].arn
  runtime                        = "python3.12"
  handler                        = local.handlers[each.key]
  filename                       = var.lambda_package_path
  source_code_hash               = filebase64sha256(var.lambda_package_path)
  timeout                        = local.timeouts[each.key]
  memory_size                    = 512
  reserved_concurrent_executions = 20
  kms_key_arn                    = var.platform_kms_key_arn

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      RF_MODE              = "aws"
      RF_DOCUMENTS_BUCKET  = var.bucket_name
      RF_MAIN_TABLE        = var.table_names["main"]
      RF_AUDIT_TABLE       = var.table_names["audit"]
      RF_IDEMPOTENCY_TABLE = var.table_names["idempotency"]
      RF_TENANT_ROLE_ARN   = aws_iam_role.tenant_data.arn
      RF_TENANT_KMS_KEYS   = jsonencode(var.tenant_kms_key_arns)
      # Built from the name to avoid a cycle (the state machine references these functions).
      RF_STATE_MACHINE_ARN = "arn:aws:states:${var.region}:${data.aws_caller_identity.current.account_id}:stateMachine:${local.name}-pipeline"
    }
  }

  depends_on = [aws_cloudwatch_log_group.fn]
}

resource "aws_lambda_event_source_mapping" "ingest" {
  event_source_arn        = aws_sqs_queue.ingest.arn
  function_name           = aws_lambda_function.fn["ingest"].arn
  batch_size              = 10
  function_response_types = ["ReportBatchItemFailures"]
}

# --- State machine -----------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "sfn" {
  name              = "/aws/vendedlogs/states/${local.name}-pipeline"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.platform_kms_key_arn
}

data "aws_iam_policy_document" "sfn_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sfn" {
  name               = "${local.name}-pipeline-sfn"
  assume_role_policy = data.aws_iam_policy_document.sfn_trust.json
}

data "aws_iam_policy_document" "sfn" {
  statement {
    sid       = "InvokePipelineFunctions"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.fn["step"].arn, aws_lambda_function.fn["await-review"].arn]
  }
  statement {
    sid = "VendedLogDelivery"
    actions = [
      "logs:CreateLogDelivery", "logs:GetLogDelivery", "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery", "logs:ListLogDeliveries", "logs:PutResourcePolicy",
      "logs:DescribeResourcePolicies", "logs:DescribeLogGroups",
    ]
    resources = ["*"] # log delivery APIs do not support resource-level permissions
  }
  statement {
    sid       = "Tracing"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"]
    resources = ["*"]
  }
  statement {
    sid       = "EncryptedExecutionData"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [var.platform_kms_key_arn]
  }
}

resource "aws_iam_role_policy" "sfn" {
  name   = "pipeline"
  role   = aws_iam_role.sfn.id
  policy = data.aws_iam_policy_document.sfn.json
}

resource "aws_sfn_state_machine" "pipeline" {
  # checkov:skip=CKV_AWS_285:Transitions are logged at level ALL; execution payloads and error causes are deliberately excluded so document data cannot reach logs (project rule: no sensitive data in logs).
  name     = "${local.name}-pipeline"
  role_arn = aws_iam_role.sfn.arn
  type     = "STANDARD"
  definition = templatefile("${path.module}/asl.json.tftpl", {
    step_function_arn         = aws_lambda_function.fn["step"].arn
    await_review_function_arn = aws_lambda_function.fn["await-review"].arn
    review_timeout_seconds    = var.review_timeout_seconds
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.sfn.arn}:*"
    include_execution_data = false # payloads stay out of logs (docs/security.md)
    level                  = "ALL" # every transition, still without payloads
  }

  tracing_configuration {
    enabled = true
  }

  encryption_configuration {
    type                              = "CUSTOMER_MANAGED_KMS_KEY"
    kms_key_id                        = var.platform_kms_key_arn
    kms_data_key_reuse_period_seconds = 300
  }
}

output "state_machine_arn" {
  description = "Pipeline state machine ARN."
  value       = aws_sfn_state_machine.pipeline.arn
}

output "ingest_queue_url" {
  description = "Ingest queue URL."
  value       = aws_sqs_queue.ingest.id
}

output "ingest_dlq_name" {
  description = "Ingest DLQ name (alarmed in W6)."
  value       = aws_sqs_queue.ingest_dlq.name
}

output "tenant_data_role_arn" {
  description = "Role assumed per tenant with session tag tenant=<id>."
  value       = aws_iam_role.tenant_data.arn
}
