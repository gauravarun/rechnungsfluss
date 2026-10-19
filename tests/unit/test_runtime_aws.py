"""AwsRuntime wiring against moto: tenant sessions, inbox reads, idempotent starts.

moto accepts any AssumeRole and does not evaluate the tenant-data policy; these
tests cover the wiring, not IAM enforcement (see test_iam_policies.py).
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import boto3
import pytest
from moto import mock_aws

from rechnungsfluss.domain.tenancy import TenantId
from rechnungsfluss.handlers.runtime import AwsRuntime, AwsSettings, ConfigurationError

REGION = "eu-central-1"


@pytest.fixture
def runtime(monkeypatch: pytest.MonkeyPatch) -> Iterator[AwsRuntime]:
    for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.setenv(var, "testing")
    with mock_aws():
        boto3.client("s3", region_name=REGION).create_bucket(
            Bucket="rf-dev-docs", CreateBucketConfiguration={"LocationConstraint": "eu-central-1"}
        )
        iam = boto3.client("iam", region_name=REGION)
        role = iam.create_role(RoleName="rf-dev-tenant-data", AssumeRolePolicyDocument="{}")
        sfn_role = iam.create_role(RoleName="rf-dev-sfn", AssumeRolePolicyDocument="{}")
        machine = boto3.client("stepfunctions", region_name=REGION).create_state_machine(
            name="rf-dev-pipeline",
            definition=json.dumps({"StartAt": "Done", "States": {"Done": {"Type": "Succeed"}}}),
            roleArn=sfn_role["Role"]["Arn"],
        )
        yield AwsRuntime(
            settings=AwsSettings(
                region=REGION,
                bucket="rf-dev-docs",
                main_table="m",
                audit_table="a",
                idempotency_table="i",
                tenant_role_arn=role["Role"]["Arn"],
                state_machine_arn=machine["stateMachineArn"],
                tenant_kms_keys={
                    "nordwerk-de": "arn:aws:kms:eu-central-1:0:key/de",
                    "nordwerk-at": "arn:aws:kms:eu-central-1:0:key/at",
                },
            )
        )


def test_known_tenants_come_from_configuration(runtime: AwsRuntime) -> None:
    assert runtime.known_tenants() == {"nordwerk-de", "nordwerk-at"}


def test_start_execution_never_creates_a_second_execution(runtime: AwsRuntime) -> None:
    payload = {"tenant": "nordwerk-de", "invoice_id": "abc"}
    runtime.start_execution("nordwerk-de-abc", payload)
    runtime.start_execution("nordwerk-de-abc", payload)  # SQS redelivery
    sfn = boto3.client("stepfunctions", region_name=REGION)
    executions = sfn.list_executions(stateMachineArn=runtime.settings.state_machine_arn)
    assert [e["name"] for e in executions["executions"]] == ["nordwerk-de-abc"]


def test_inbox_read_uses_a_tenant_session(runtime: AwsRuntime) -> None:
    boto3.client("s3", region_name=REGION).put_object(
        Bucket="rf-dev-docs", Key="nordwerk-de/inbox/a.xml", Body=b"<x/>"
    )
    assert (
        runtime.read_inbox_object(TenantId("nordwerk-de"), "rf-dev-docs", "nordwerk-de/inbox/a.xml")
        == b"<x/>"
    )
    assert "nordwerk-de" in runtime._sessions  # session cached per tenant


def test_events_for_other_buckets_are_refused(runtime: AwsRuntime) -> None:
    with pytest.raises(ConfigurationError, match="unexpected bucket"):
        runtime.read_inbox_object(TenantId("nordwerk-de"), "someone-else", "nordwerk-de/inbox/a")


def test_missing_validator_fails_loudly(runtime: AwsRuntime) -> None:
    pipeline = runtime.pipeline(TenantId("nordwerk-de"))
    with pytest.raises(ConfigurationError, match="validator is not configured"):
        pipeline.s.validator.validate(b"<x/>")


def test_settings_require_every_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RF_DOCUMENTS_BUCKET", raising=False)
    monkeypatch.setenv("AWS_REGION", REGION)
    with pytest.raises(ConfigurationError, match="RF_DOCUMENTS_BUCKET"):
        AwsSettings.from_env()
