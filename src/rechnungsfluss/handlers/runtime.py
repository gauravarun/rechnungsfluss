"""AWS wiring for the Lambda handlers (composition root).

Tenant isolation, layer 2 (ADR-0006): every tenant's data is accessed through
a boto3 session obtained by assuming the tenant-data role with session tag
``tenant=<id>``. That role's policy (infra/terraform/modules/pipeline/policies)
only allows DynamoDB keys starting ``TENANT#<id>#`` and S3 objects under
``<id>/``. The Lambda execution role itself has no data access.

**Status:** statically checked and unit-tested with fakes and moto; not yet
exercised in a real AWS account (see README "Known limits").
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import boto3

from rechnungsfluss.adapters.aws import (
    DynamoAuditLog,
    DynamoFingerprintStore,
    DynamoIdempotencyStore,
    DynamoInvoiceRepository,
    DynamoPurchaseOrderRepository,
    S3ObjectStore,
)
from rechnungsfluss.adapters.local import utc_now
from rechnungsfluss.application.pipeline import Pipeline, Services
from rechnungsfluss.domain.tenancy import TenantId
from rechnungsfluss.ports import ErpClient, Extractor, Validator

if TYPE_CHECKING:
    from boto3.session import Session

SESSION_SECONDS = 900
_REFRESH_MARGIN_S = 120


class ConfigurationError(RuntimeError):
    pass


def _env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise ConfigurationError(f"environment variable {name} is not set")
    return value


@dataclass(frozen=True, slots=True)
class AwsSettings:
    region: str
    bucket: str
    main_table: str
    audit_table: str
    idempotency_table: str
    tenant_role_arn: str
    state_machine_arn: str
    tenant_kms_keys: Mapping[str, str]
    """tenant -> KMS key ARN for S3 objects (layer 3, ADR-0006)."""

    @classmethod
    def from_env(cls) -> AwsSettings:
        return cls(
            region=_env("AWS_REGION"),
            bucket=_env("RF_DOCUMENTS_BUCKET"),
            main_table=_env("RF_MAIN_TABLE"),
            audit_table=_env("RF_AUDIT_TABLE"),
            idempotency_table=_env("RF_IDEMPOTENCY_TABLE"),
            tenant_role_arn=_env("RF_TENANT_ROLE_ARN"),
            state_machine_arn=os.environ.get("RF_STATE_MACHINE_ARN", ""),
            tenant_kms_keys=json.loads(_env("RF_TENANT_KMS_KEYS")),
        )


@dataclass
class AwsRuntime:
    settings: AwsSettings
    validator: Validator | None = None
    erp: ErpClient | None = None
    extractor: Extractor | None = None
    _sessions: dict[str, tuple[float, Session]] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> AwsRuntime:
        # Validator (KoSIT Lambda container, ADR-0003), ERP client (W5) and extractor (W4)
        # are attached by their own wiring as they are built; a step that needs a missing
        # one fails with ConfigurationError and Step Functions routes it to the Fail state.
        return cls(settings=AwsSettings.from_env())

    def known_tenants(self) -> frozenset[str]:
        return frozenset(self.settings.tenant_kms_keys)

    def _tenant_session(self, tenant: TenantId) -> Session:
        cached = self._sessions.get(tenant.value)
        if cached and cached[0] - _REFRESH_MARGIN_S > time.time():
            return cached[1]
        creds = boto3.client("sts", region_name=self.settings.region).assume_role(
            RoleArn=self.settings.tenant_role_arn,
            RoleSessionName=f"rf-{tenant.value}",
            DurationSeconds=SESSION_SECONDS,
            Tags=[{"Key": "tenant", "Value": tenant.value}],
        )["Credentials"]
        session = boto3.session.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
            region_name=self.settings.region,
        )
        self._sessions[tenant.value] = (creds["Expiration"].timestamp(), session)
        return session

    def pipeline(self, tenant: TenantId) -> Pipeline:
        s, session = self.settings, self._tenant_session(tenant)
        return Pipeline(
            Services(
                store=S3ObjectStore(session, tenant, s.bucket, s.tenant_kms_keys[tenant.value]),
                repo=DynamoInvoiceRepository(session, tenant, s.main_table),
                audit=DynamoAuditLog(session, tenant, s.audit_table),
                idempotency=DynamoIdempotencyStore(session, tenant, s.idempotency_table),
                fingerprints=DynamoFingerprintStore(session, tenant, s.main_table),
                purchase_orders=DynamoPurchaseOrderRepository(session, tenant, s.main_table),
                validator=self.validator or _Missing("validator"),  # type: ignore[arg-type]
                erp=self.erp or _Missing("ERP client"),  # type: ignore[arg-type]
                extractor=self.extractor,
                clock=utc_now,
            ),
            inline_export=False,
        )

    def read_inbox_object(self, tenant: TenantId, bucket: str, key: str) -> bytes:
        if bucket != self.settings.bucket:
            raise ConfigurationError(f"event for unexpected bucket {bucket}")
        obj = self._tenant_session(tenant).client("s3").get_object(Bucket=bucket, Key=key)
        body: bytes = obj["Body"].read()
        return body

    def start_execution(self, name: str, payload: Mapping[str, str]) -> bool:
        sfn = boto3.client("stepfunctions", region_name=self.settings.region)
        try:
            sfn.start_execution(
                stateMachineArn=self.settings.state_machine_arn,
                name=name,
                input=json.dumps(dict(payload)),
            )
        except sfn.exceptions.ExecutionAlreadyExists:
            return False
        return True


class _Missing:
    """Placeholder that fails loudly when a not-yet-wired dependency is used."""

    def __init__(self, what: str) -> None:
        self._what = what

    def __getattr__(self, name: str) -> object:
        raise ConfigurationError(f"{self._what} is not configured in this environment")
