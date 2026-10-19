# Runbook

Status: **partial**. Written so far: the deployment bootstrap, the replay semantics and LLM eval operations. Alerts and dashboards arrive in W6.

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

## 4. LLM extraction: Bedrock access and evaluation (owner)
Live calls cost money and send document content to Bedrock. Use **synthetic data only** (ADR-0004, ADR-0011).

### 4.1 Enable the models (manual, Bedrock console)
1. In the sandbox account, open **Amazon Bedrock → Model access** in `eu-central-1`, or the EU region you use.
2. Request access to **Claude Opus 5.5** (primary) and **Claude Opus 4.8**. Opus 4.8 is the refusal fallback (ADR-0004 amendment).
3. If the console only offers a model through an inference profile, note the profile ID it shows. Use that ID as `RF_BEDROCK_MODEL_ID` below. **Don't guess IDs.**

### 4.2 Local credentials
Use a named AWS profile, for example from IAM Identity Center (`aws configure sso`). **Never put keys in `.env` or the repository.** In `.env`:
```
RF_BEDROCK_PROFILE=<profile name>
RF_BEDROCK_MODEL_ID=anthropic.claude-opus-5-5   # or the inference-profile ID from 4.1
```

### 4.3 Smoke test (1 document)
```bash
python -m rechnungsfluss.evaluation.extraction --mode live --limit 1
```
- **Expected:** a report in `eval/reports/` with one document, plus its token usage.
- **`AccessDenied` naming an IAM action:** add that action to your user policy, and also to `infra/terraform/modules/ci-eval` (the CI role). The Messages ("Mantle") endpoint's exact actions haven't been verified yet.
- **Model not found:** use the inference-profile ID from 4.1.

### 4.4 First baseline (about 30 documents)
Start the validator, then run the live eval once, with recording and the routing check:
```bash
scripts/kosit.sh start &      # validator for the routing check
python -m rechnungsfluss.evaluation.extraction --mode live --record --routing --write-baseline
```
This writes:
- `eval/cassettes/*.json` (replayed by every PR from then on),
- `eval/baselines/extraction.json`,
- the extraction report and the end-to-end routing report (`*-routing-llm-live.md`).

Commit all of them through a PR. Then put the measured numbers into the README, copied from the report files and never retyped.

### 4.5 Owner decision: accuracy threshold (ADR-0010)
After reading the baseline report, set the minimum field accuracy:
File `eval/thresholds.json`:
```json
{"extraction_field_accuracy_min": 0.97}
```
The value above is only an example. **The owner chooses it.**

From then on, PR CI fails if any of these happens:
- accuracy falls below the threshold,
- accuracy drops more than 2 pp below the baseline,
- silent errors rise above the baseline,
- any document is falsely auto-approved.

### 4.6 Nightly live eval in CI (GitHub OIDC, no stored keys)
1. Apply `module.ci_eval` (section 1.3). Its output `ci_eval_role_arn` is a role that only main-branch workflows of this repository can assume. It may only invoke the configured Claude models.
2. In GitHub → Settings → Secrets and variables → **Variables**, set:
   - `AWS_EVAL_ROLE_ARN`
   - `AWS_EVAL_REGION`
   - optionally `RF_BEDROCK_MODEL_ID`
3. The `eval / live` job then runs nightly and on manual dispatch. It records fresh cassettes and reports as a build artifact. To update the committed cassettes, download the artifact and open a PR. GitHub doesn't run checks on PRs created by its own token, so the job can't open the PR itself.

### 4.7 If the live eval fails
| Symptom | First checks |
|---|---|
| `GATE FAILED: false accepts` | Open the `routing-llm` report and find the auto-approved document. Something the extractor got wrong passed validation and matching. Treat it as a defect, not noise. |
| Accuracy below the baseline | Did the model ID, prompt version or effort change? Compare the per-field tables between reports. |
| Throttling or timeouts | The SDK retries twice by default. Re-run, or lower concurrency. |
| `ExtractionRefusedError` | The fallback model also declined. Check the document. Refusals are expected to be rare for invoices. |
