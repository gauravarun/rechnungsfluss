"""Tenant isolation, IAM layer (ADR-0006): checks on the policy document itself.

moto does not evaluate IAM, so these tests are the strongest check possible
without a real AWS account: every data permission is scoped to the caller's
``tenant`` session tag, and the audit table is append-only.
"""

from typing import Any

import pytest

from rechnungsfluss.domain.tenancy import TenantId, entity_key, tenant_prefix
from tests.terraform_templates import PIPELINE_MODULE, render

ARN = "arn:aws:dynamodb:eu-central-1:000000000000:table/rf-dev-"
TAG = "${aws:PrincipalTag/tenant}"


@pytest.fixture(scope="module")
def policy() -> dict[str, Any]:
    doc: dict[str, Any] = render(
        PIPELINE_MODULE / "policies" / "tenant-data.json.tftpl",
        {
            "main_table_arn": ARN + "main",
            "audit_table_arn": ARN + "audit",
            "idempotency_table_arn": ARN + "idempotency",
            "bucket_arn": "arn:aws:s3:::rf-dev-docs",
            "tenant_kms_key_arns_json": '["arn:aws:kms:eu-central-1:000000000000:key/de",'
            ' "arn:aws:kms:eu-central-1:000000000000:key/at"]',
            "platform_kms_key_arn": "arn:aws:kms:eu-central-1:000000000000:key/platform",
            "region": "eu-central-1",
        },
    )
    return doc


def _statements(policy: dict[str, Any], effect: str) -> list[dict[str, Any]]:
    return [s for s in policy["Statement"] if s["Effect"] == effect]


def test_every_dynamodb_allow_is_limited_to_the_callers_tenant_keys(policy: dict[str, Any]) -> None:
    ddb = [
        s
        for s in _statements(policy, "Allow")
        if any(a.startswith("dynamodb:") for a in s["Action"])
    ]
    assert ddb
    for stmt in ddb:
        leading = stmt["Condition"]["ForAllValues:StringLike"]["dynamodb:LeadingKeys"]
        assert leading == [f"TENANT#{TAG}#*"], stmt["Sid"]


def test_leading_key_pattern_matches_the_keys_the_code_builds() -> None:
    """The IAM pattern and domain/tenancy.py must agree, or isolation silently breaks."""
    tenant = TenantId("nordwerk-de")
    pattern_prefix = f"TENANT#{TAG}#".replace(TAG, tenant.value)
    assert tenant_prefix(tenant) == pattern_prefix
    assert entity_key(tenant, "INV", "abc").startswith(pattern_prefix)
    assert entity_key(TenantId("nordwerk-at"), "INV", "abc").startswith(pattern_prefix) is False


def test_no_destructive_dynamodb_actions_are_allowed(policy: dict[str, Any]) -> None:
    allowed = {a for s in _statements(policy, "Allow") for a in s["Action"]}
    assert not allowed & {
        "dynamodb:DeleteItem",
        "dynamodb:UpdateItem",
        "dynamodb:Scan",
        "dynamodb:BatchWriteItem",
        "dynamodb:DeleteTable",
        "dynamodb:*",
    }


def test_audit_table_is_append_only(policy: dict[str, Any]) -> None:
    denies = [s for s in _statements(policy, "Deny") if ARN + "audit" in s["Resource"]]
    denied = {a for s in denies for a in s["Action"]}
    assert {"dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:BatchWriteItem"} <= denied


def test_s3_access_is_limited_to_the_tenant_prefix(policy: dict[str, Any]) -> None:
    s3 = [s for s in _statements(policy, "Allow") if any(a.startswith("s3:") for a in s["Action"])]
    assert len(s3) == 1
    assert s3[0]["Resource"] == [f"arn:aws:s3:::rf-dev-docs/{TAG}/*"]
    assert set(s3[0]["Action"]) == {"s3:GetObject", "s3:PutObject"}


def test_tenant_kms_keys_require_matching_tag(policy: dict[str, Any]) -> None:
    kms = next(s for s in policy["Statement"] if s["Sid"] == "TenantKmsKeyOnly")
    assert kms["Condition"]["StringEquals"]["aws:ResourceTag/tenant"] == TAG


def test_no_allow_statement_has_an_unconditioned_wildcard_resource(policy: dict[str, Any]) -> None:
    for stmt in _statements(policy, "Allow"):
        assert "*" not in stmt["Resource"], stmt["Sid"]
        assert not any(a.endswith(":*") for a in stmt["Action"]), stmt["Sid"]
