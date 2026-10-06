# pyright: reportMissingTypeStubs=false

"""Read-only IAM simulator checks against the exact reviewed operator plan."""

import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import (  # type: ignore[import-untyped]
    BotoCoreError,
    ClientError,
)

from scripts.aws_watcher_operator_plan_guard import (
    ACCOUNT,
    is_production_provider_update,
    is_read_preflight_update,
    mapping,
    sequence,
    validate_plan,
)

_BUCKET = f"arn:aws:s3:::minos-health-snapshots-{ACCOUNT}-eu-central-1"
_ACCEPTANCE_ROLE = f"arn:aws:iam::{ACCOUNT}:role/minos-health-watcher-acceptance-0123456789abcdef-watcher"
_WATCHER_USER = f"arn:aws:iam::{ACCOUNT}:user/minos-health-watcher"
_PRODUCTION_BOUNDARY = f"arn:aws:iam::{ACCOUNT}:policy/minos-health-watcher-boundary"
_ACCEPTANCE_BOUNDARY = (
    f"arn:aws:iam::{ACCOUNT}:policy/minos-health-watcher-acceptance-boundary"
)


@dataclass(frozen=True, slots=True)
class Scenario:
    policy: str
    action: str
    resource: str
    expected: str
    context: tuple[tuple[str, str], ...] = ()


class _IamSimulator(Protocol):
    def simulate_custom_policy(self, **kwargs: object) -> Mapping[str, object]: ...


class _StsClient(Protocol):
    def get_caller_identity(self) -> Mapping[str, object]: ...


class _AwsSession(Protocol):
    def client(self, service_name: str, *, config: object | None = None) -> object: ...


_ACCEPTANCE_TAGS = (
    ("aws:RequestTag/Disposable", "true"),
    ("aws:RequestTag/Environment", "acceptance"),
    ("aws:RequestTag/ManagedBy", "Terraform"),
    ("aws:RequestTag/Project", "minos-health"),
    ("aws:RequestTag/TestHarness", "watcher-acceptance"),
    ("aws:RequestTag/TestRunId", "0123456789abcdef"),
    ("aws:TagKeys", "Disposable"),
)

READ_PREFLIGHT_SCENARIOS = (
    Scenario(
        "acceptance_operator_boundary",
        "s3:GetBucketCORS",
        f"arn:aws:s3:::minos-health-watcher-acceptance-{ACCOUNT}-0123456789abcdef",
        "allowed",
    ),
    Scenario(
        "acceptance_operator_boundary",
        "s3:GetBucketCORS",
        "arn:aws:s3:::unrelated-bucket",
        "implicitDeny",
    ),
    Scenario(
        "acceptance_operator_boundary",
        "iam:ListAttachedRolePolicies",
        _ACCEPTANCE_ROLE,
        "allowed",
        (
            ("aws:ResourceTag/Disposable", "true"),
            ("aws:ResourceTag/Environment", "acceptance"),
            ("aws:ResourceTag/Project", "minos-health"),
            ("aws:ResourceTag/TestHarness", "watcher-acceptance"),
        ),
    ),
    Scenario(
        "acceptance_operator_boundary",
        "iam:ListAttachedRolePolicies",
        _ACCEPTANCE_ROLE,
        "implicitDeny",
    ),
    Scenario(
        "acceptance_operator_boundary", "iam:GetRole", _ACCEPTANCE_ROLE, "allowed"
    ),
    Scenario(
        "acceptance_operator_boundary",
        "iam:GetRole",
        f"arn:aws:iam::{ACCOUNT}:role/AdministratorAccess",
        "implicitDeny",
    ),
    Scenario(
        "acceptance_operator_boundary",
        "iam:DeleteRole",
        _ACCEPTANCE_ROLE,
        "implicitDeny",
    ),
)

ACCEPTANCE_PROVIDER_SCENARIOS = tuple(
    Scenario("acceptance_operator_boundary", action, resource, expected)
    for action in ("s3:GetAccelerateConfiguration", "s3:GetReplicationConfiguration")
    for resource, expected in (
        (
            f"arn:aws:s3:::minos-health-watcher-acceptance-{ACCOUNT}-0123456789abcdef",
            "allowed",
        ),
        ("arn:aws:s3:::unrelated-bucket", "implicitDeny"),
    )
)

PRODUCTION_PROVIDER_SCENARIOS = (
    Scenario(
        "production_operator_boundary",
        "s3:GetAccelerateConfiguration",
        _BUCKET,
        "allowed",
    ),
    Scenario(
        "production_operator_boundary",
        "s3:GetReplicationConfiguration",
        _BUCKET,
        "allowed",
    ),
    Scenario(
        "production_operator_boundary",
        "s3:GetAccelerateConfiguration",
        "arn:aws:s3:::unrelated-bucket",
        "implicitDeny",
    ),
    Scenario(
        "production_operator_boundary",
        "budgets:ModifyBudget",
        f"arn:aws:budgets::{ACCOUNT}:budget/minos-health-watcher-monthly",
        "allowed",
    ),
    Scenario(
        "production_operator_boundary",
        "budgets:ViewBudget",
        f"arn:aws:budgets::{ACCOUNT}:budget/minos-health-watcher-monthly",
        "allowed",
    ),
    Scenario(
        "production_operator_boundary",
        "sns:GetSubscriptionAttributes",
        "*",
        "allowed",
        (("aws:RequestedRegion", "eu-central-1"),),
    ),
    Scenario(
        "production_operator_boundary",
        "sns:GetSubscriptionAttributes",
        "*",
        "implicitDeny",
        (("aws:RequestedRegion", "us-east-1"),),
    ),
    Scenario(
        "production_operator_boundary",
        "s3:GetBucketCORS",
        _BUCKET,
        "allowed",
    ),
    Scenario(
        "production_operator_boundary",
        "s3:GetBucketCORS",
        "arn:aws:s3:::unrelated-bucket",
        "implicitDeny",
    ),
    Scenario(
        "production_operator_boundary",
        "iam:ListAttachedRolePolicies",
        f"arn:aws:iam::{ACCOUNT}:role/minos-health-watcher-github-archive-reader",
        "allowed",
    ),
    Scenario(
        "production_operator_boundary",
        "iam:ListAttachedRolePolicies",
        f"arn:aws:iam::{ACCOUNT}:role/AdministratorAccess",
        "implicitDeny",
    ),
)

SCENARIOS = (
    Scenario(
        "acceptance_operator_boundary",
        "iam:CreateRole",
        _ACCEPTANCE_ROLE,
        "allowed",
        (("iam:PermissionsBoundary", _ACCEPTANCE_BOUNDARY), *_ACCEPTANCE_TAGS),
    ),
    Scenario(
        "acceptance_operator_boundary",
        "iam:CreateRole",
        _ACCEPTANCE_ROLE,
        "implicitDeny",
        (
            (
                "iam:PermissionsBoundary",
                "arn:aws:iam::218375352108:policy/AdministratorAccess",
            ),
            *_ACCEPTANCE_TAGS,
        ),
    ),
    Scenario(
        "acceptance_operator_boundary",
        "iam:CreateAccessKey",
        _WATCHER_USER,
        "implicitDeny",
    ),
    Scenario(
        "production_operator_boundary",
        "iam:PassRole",
        f"arn:aws:iam::{ACCOUNT}:role/AdministratorAccess",
        "implicitDeny",
    ),
    Scenario(
        "production_operator_boundary",
        "iam:CreateUser",
        _WATCHER_USER,
        "allowed",
        (
            ("iam:PermissionsBoundary", _PRODUCTION_BOUNDARY),
            ("aws:RequestTag/Project", "minos-health"),
        ),
    ),
    Scenario(
        "production_operator_boundary",
        "iam:CreateUser",
        _WATCHER_USER,
        "implicitDeny",
        (
            (
                "iam:PermissionsBoundary",
                "arn:aws:iam::218375352108:policy/AdministratorAccess",
            ),
            ("aws:RequestTag/Project", "minos-health"),
        ),
    ),
    Scenario(
        "production_operator_boundary",
        "s3:CreateBucket",
        _BUCKET,
        "allowed",
        (("s3:LocationConstraint", "eu-central-1"),),
    ),
    Scenario(
        "production_operator_boundary",
        "budgets:CreateBudget",
        f"arn:aws:budgets::{ACCOUNT}:budget/minos-health-watcher-monthly",
        "allowed",
    ),
    Scenario(
        "production_operator_boundary",
        "sns:Subscribe",
        f"arn:aws:sns:eu-central-1:{ACCOUNT}:minos-health-watcher-budget-alerts",
        "allowed",
    ),
    Scenario(
        "watcher_boundary",
        "s3:PutObject",
        f"{_BUCKET}/blobs/sha256/example",
        "allowed",
        (("s3:if-none-match", "*"),),
    ),
    Scenario(
        "watcher_boundary",
        "s3:PutObject",
        f"{_BUCKET}/blobs/sha256/example",
        "implicitDeny",
    ),
    Scenario(
        "watcher_boundary",
        "s3:DeleteObject",
        f"{_BUCKET}/blobs/sha256/example",
        "implicitDeny",
    ),
    Scenario(
        "github_reader_boundary",
        "s3:GetObject",
        f"{_BUCKET}/blobs/sha256/example",
        "allowed",
    ),
    Scenario(
        "github_reader_boundary",
        "s3:PutObject",
        f"{_BUCKET}/blobs/sha256/example",
        "implicitDeny",
    ),
    Scenario(
        "github_reader_boundary",
        "s3:GetObject",
        "arn:aws:s3:::unrelated-bucket/private",
        "implicitDeny",
    ),
    Scenario(
        "state_recovery_boundary",
        "s3:DeleteObjectVersion",
        f"arn:aws:s3:::minos-health-tfstate-{ACCOUNT}-eu-central-1/watcher/terraform.tfstate",
        "implicitDeny",
    ),
    Scenario(
        "acceptance_operator_boundary",
        "iam:CreateRole",
        _ACCEPTANCE_ROLE,
        "implicitDeny",
        (("iam:PermissionsBoundary", _ACCEPTANCE_BOUNDARY), *_ACCEPTANCE_TAGS[:-2]),
    ),
    Scenario(
        "acceptance_operator_boundary",
        "s3:CreateBucket",
        "arn:aws:s3:::unrelated-bucket",
        "implicitDeny",
        (("s3:LocationConstraint", "eu-central-1"),),
    ),
    Scenario(
        "production_operator_boundary",
        "iam:CreateAccessKey",
        _WATCHER_USER,
        "implicitDeny",
    ),
    Scenario(
        "production_operator_boundary",
        "s3:CreateBucket",
        "arn:aws:s3:::unrelated-bucket",
        "implicitDeny",
        (("s3:LocationConstraint", "eu-central-1"),),
    ),
    Scenario(
        "state_recovery_boundary",
        "s3:PutObject",
        f"arn:aws:s3:::minos-health-tfstate-{ACCOUNT}-eu-central-1/watcher/terraform.tfstate",
        "allowed",
        (("s3:if-match", "reviewed-etag"),),
    ),
    Scenario(
        "state_recovery_boundary",
        "s3:PutObject",
        f"arn:aws:s3:::minos-health-tfstate-{ACCOUNT}-eu-central-1/watcher/terraform.tfstate",
        "implicitDeny",
    ),
    Scenario(
        "state_recovery_boundary",
        "s3:GetObject",
        "arn:aws:s3:::unrelated-bucket/private",
        "implicitDeny",
    ),
)


def planned_policies(document: object) -> dict[str, str]:
    validate_plan(document)
    if is_read_preflight_update(document):
        root = mapping(document, "operator plan")
        planned = mapping(root.get("planned_values"), "planned values")
        module = mapping(planned.get("root_module"), "root module")
        resources = sequence(module.get("resources"), "planned resources")
        updated_policies: dict[str, str] = {}
        for item in resources:
            resource = mapping(item, "planned resource")
            address = resource.get("address")
            if not isinstance(address, str) or not address.startswith(
                "aws_iam_policy."
            ):
                continue
            value = mapping(resource.get("values"), "planned policy values").get(
                "policy"
            )
            if not isinstance(value, str):
                raise ValueError("operator boundary is unavailable")
            updated_policies[address.split(".")[-1]] = value
        return updated_policies
    if not isinstance(document, dict):
        raise ValueError("operator plan is not an object")
    root = cast(dict[str, object], document)
    raw = root.get("resource_changes")
    if not isinstance(raw, list):
        raise ValueError("operator plan has no changes")
    policies: dict[str, str] = {}
    for item in cast(list[object], raw):
        if not isinstance(item, dict):
            continue
        record = cast(dict[str, object], item)
        address = record.get("address")
        if not isinstance(address, str) or not address.startswith("aws_iam_policy."):
            continue
        change = record.get("change")
        if not isinstance(change, dict):
            raise ValueError("operator policy change is invalid")
        after = cast(dict[str, object], change).get("after")
        if not isinstance(after, dict):
            raise ValueError("operator policy is unknown")
        policy = cast(dict[str, object], after).get("policy")
        if not isinstance(policy, str):
            raise ValueError("operator policy is unavailable")
        policies[address.split(".")[-1]] = policy
    return policies


def evaluate(
    client: _IamSimulator, policies: dict[str, str], scenario: Scenario
) -> None:
    if scenario.policy not in policies:
        raise ValueError("operator policy is absent")
    arguments: dict[str, object] = {
        "PolicyInputList": [policies[scenario.policy]],
        "ActionNames": [scenario.action],
        "ResourceArns": [scenario.resource],
        "MaxItems": 2,
    }
    if scenario.context:
        arguments["ContextEntries"] = [
            {
                "ContextKeyName": name,
                "ContextKeyValues": [value],
                "ContextKeyType": "string",
            }
            for name, value in scenario.context
        ]
    result = mapping(
        client.simulate_custom_policy(**arguments), "IAM simulator response"
    )
    evaluations = sequence(result.get("EvaluationResults"), "IAM evaluations")
    if result.get("IsTruncated") or len(evaluations) != 1:
        raise ValueError("IAM simulator response is ambiguous")
    decision = mapping(evaluations[0], "IAM evaluation").get("EvalDecision")
    if decision != scenario.expected:
        raise ValueError("IAM simulator decision differs from the reviewed allowlist")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: aws_watcher_operator_simulate.py PLAN_JSON", file=sys.stderr)
        return 2
    try:
        path = Path(sys.argv[1])
        if not path.is_file() or path.stat().st_size > 2_000_000:
            raise ValueError("operator plan is unavailable or oversized")
        document = cast(object, json.loads(path.read_text()))
        policies = planned_policies(document)
        session = cast(
            _AwsSession,
            boto3.Session(
                profile_name="minos-health-bootstrap", region_name="eu-central-1"
            ),
        )
        identity = cast(_StsClient, session.client("sts")).get_caller_identity()
        if identity.get("Account") != ACCOUNT:
            raise ValueError("IAM simulator caller is outside the reviewed account")
        client = cast(
            _IamSimulator,
            session.client(
                "iam",
                config=Config(
                    connect_timeout=5, read_timeout=20, retries={"max_attempts": 2}
                ),
            ),
        )
        scenarios = (
            SCENARIOS
            + ACCEPTANCE_PROVIDER_SCENARIOS
            + (READ_PREFLIGHT_SCENARIOS if is_read_preflight_update(document) else ())
            + (
                PRODUCTION_PROVIDER_SCENARIOS
                if is_production_provider_update(document)
                else ()
            )
        )
        for scenario in scenarios:
            evaluate(client, policies, scenario)
    except (OSError, ValueError, BotoCoreError, ClientError) as error:
        print(f"IAM simulator checks failed: {type(error).__name__}", file=sys.stderr)
        return 1
    print(f"IAM simulator confirmed {len(scenarios)} bounded allow and deny decisions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
