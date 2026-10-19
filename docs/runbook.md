# Runbook

Status: **partial**. The deployment bootstrap is written down. Alerts, dashboards and the replay procedure are added together with their features (W3–W6), and none of them exist yet.

## 1. Deploying the sandbox environment (owner only)
CI never applies Terraform. Only the account owner applies it, from their own machine, to a **sandbox** AWS account that holds no real data.

### 1.1 One-time bootstrap (manual, before the first `init`)
Terraform state is stored in S3 with native lockfiles (`use_lockfile = true`), so no DynamoDB lock table is needed. The state bucket is created once by hand:

```bash
export AWS_PROFILE=<sandbox-admin-profile>
export TF_STATE_BUCKET=rechnungsfluss-tfstate-<account-id>
aws s3api create-bucket --bucket "$TF_STATE_BUCKET" --region eu-central-1 \
  --create-bucket-configuration LocationConstraint=eu-central-1
aws s3api put-bucket-versioning --bucket "$TF_STATE_BUCKET" \
  --versioning-configuration Status=Enabled
aws s3api put-public-access-block --bucket "$TF_STATE_BUCKET" \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-encryption --bucket "$TF_STATE_BUCKET" \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"aws:kms"}}]}'
```

### 1.2 Build the Lambda package
```bash
scripts/build_lambda.sh        # -> dist/lambda.zip (about 36 MB; Lambda direct-upload limit is 50 MB)
```
The package is built from `uv.lock` (runtime dependencies only) as Linux `manylinux_2_28` wheels for the python3.12 runtime. If it ever grows past 50 MB, upload it to S3 and switch the functions to `s3_bucket` / `s3_key`.

### 1.3 Plan and apply
```bash
cd infra/terraform/envs/aws-dev
terraform init -backend-config="bucket=$TF_STATE_BUCKET" \
  -backend-config="key=aws-dev/terraform.tfstate" -backend-config="region=eu-central-1"
terraform plan -out=tfplan
terraform apply tfplan
```

### 1.4 Tear-down
Tables have `deletion_protection_enabled = true`, and the document bucket is versioned. To tear down:
1. Set `deletion_protection_enabled = false`, then apply.
2. Empty the bucket (all versions).
3. Run `terraform destroy`.

KMS keys enter a 30-day pending-deletion window and can be cancelled during that time.

### 1.5 What does not work yet after an apply
- **Validate step:** fails with `ConfigurationError` (validator not configured) until the KoSIT Lambda container exists (W6). The execution ends in `StepFailed`.
- **Export step:** same, until the ERP client is wired (W5).
- **Review:** an invoice in review waits for `SendTaskSuccess` from the review API (W5), at most 30 days (`review_timeout_seconds`).

## 2. Alerts
*Not built yet (W6).* Each alert will get an entry here covering threshold, meaning, first checks and remediation.

## 3. Replaying failed items
*The replay CLI arrives in W5. The logic exists today as `Pipeline.replay`.*

| State | Meaning | Replay does |
|---|---|---|
| `FAILED` | A parse or extraction step failed. The error is in `meta.error`. | Back to `RECEIVED`, then the full pipeline runs again. Audited as `REPLAYED`. |
| `EXPORT_FAILED` | The ERP was unavailable. | Re-posts with the **same** idempotency key, so the ERP can't book the invoice twice. |

Healthy invoices can't be replayed. Replay refuses them.
