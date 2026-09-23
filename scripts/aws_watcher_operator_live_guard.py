# pyright: reportMissingTypeStubs=false

"""Verify the actual IAM boundaries and state recovery path after an approved apply."""

import json
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, cast
from urllib.parse import unquote

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import (  # type: ignore[import-untyped]
    BotoCoreError,
    ClientError,
)

from scripts.aws_watcher_operator_plan_guard import (
    ACCOUNT,
    POLICY_NAMES,
    ROLE_NAMES,
    mapping,
    sequence,
    validate_plan,
)

_REGION = "eu-central-1"
_STATE_BUCKET = f"minos-health-tfstate-{ACCOUNT}-{_REGION}"
_STATE_KEY = "watcher-operators/terraform.tfstate"
_ROLE_POLICY_NAMES = {
    "production_operator": "minos-health-watcher-terraform-scoped",
    "acceptance_operator": "minos-health-watcher-acceptance-scoped",
    "state_recovery": "minos-health-watcher-state-recovery-scoped",
}


class _IamClient(Protocol):
    def get_role(self, **kwargs: object) -> Mapping[str, object]: ...

    def get_role_policy(self, **kwargs: object) -> Mapping[str, object]: ...

    def get_policy(self, **kwargs: object) -> Mapping[str, object]: ...

    def get_policy_version(self, **kwargs: object) -> Mapping[str, object]: ...

    def list_attached_role_policies(self, **kwargs: object) -> Mapping[str, object]: ...

    def simulate_principal_policy(self, **kwargs: object) -> Mapping[str, object]: ...


class _StsClient(Protocol):
    def get_caller_identity(self) -> Mapping[str, object]: ...

    def assume_role(self, **kwargs: object) -> Mapping[str, object]: ...


class _S3Client(Protocol):
    def head_object(self, **kwargs: object) -> Mapping[str, object]: ...


class _Session(Protocol):
    def client(
        self,
        service_name: str,
        *,
        config: object | None = None,
        aws_access_key_id: object | None = None,
        aws_secret_access_key: object | None = None,
        aws_session_token: object | None = None,
    ) -> object: ...


def _document(value: object, label: str) -> dict[str, object]:
    if isinstance(value, str):
        return mapping(cast(object, json.loads(unquote(value))), label)
    return mapping(value, label)


def _role_name_and_boundary(key: str) -> tuple[str, str]:
    name = ROLE_NAMES[key]
    boundary = f"arn:aws:iam::{ACCOUNT}:policy/{POLICY_NAMES[key + '_boundary']}"
    return name, boundary


def verify_role(iam: _IamClient, key: str, expected_trust: dict[str, object]) -> str:
    name, boundary_arn = _role_name_and_boundary(key)
    role = mapping(iam.get_role(RoleName=name).get("Role"), "live role")
    expected_arn = f"arn:aws:iam::{ACCOUNT}:role/{name}"
    if role.get("Arn") != expected_arn:
        raise ValueError("live operator role ARN is unexpected")
    if (
        mapping(role.get("PermissionsBoundary"), "live boundary").get(
            "PermissionsBoundaryArn"
        )
        != boundary_arn
    ):
        raise ValueError("live operator role boundary is unexpected")
    if _document(role.get("AssumeRolePolicyDocument"), "live trust") != expected_trust:
        raise ValueError("live operator role trust differs from the reviewed plan")
    tags = sequence(role.get("Tags"), "live role tags")
    if not any(
        mapping(tag, "role tag").get("Key") == "Project"
        and mapping(tag, "role tag").get("Value") == "minos-health"
        for tag in tags
    ):
        raise ValueError("live operator role ownership tag is absent")
    attached = mapping(
        iam.list_attached_role_policies(RoleName=name, MaxItems=2), "attached policies"
    )
    if attached.get("IsTruncated") or sequence(
        attached.get("AttachedPolicies"), "attached policies"
    ):
        raise ValueError("live operator role has an unexpected managed attachment")
    return expected_arn


def verify_policy(
    iam: _IamClient, key: str, expected_policy: dict[str, object]
) -> None:
    arn = f"arn:aws:iam::{ACCOUNT}:policy/{POLICY_NAMES[key]}"
    policy = mapping(iam.get_policy(PolicyArn=arn).get("Policy"), "live policy")
    if policy.get("Arn") != arn:
        raise ValueError("live boundary ARN is unexpected")
    version = policy.get("DefaultVersionId")
    if not isinstance(version, str):
        raise ValueError("live boundary has no default version")
    response = mapping(
        iam.get_policy_version(PolicyArn=arn, VersionId=version).get("PolicyVersion"),
        "live boundary version",
    )
    if _document(response.get("Document"), "live boundary document") != expected_policy:
        raise ValueError("live permission boundary differs from the reviewed plan")


def verify_inline_policy(
    iam: _IamClient, key: str, expected_policy: dict[str, object]
) -> None:
    response = mapping(
        iam.get_role_policy(
            RoleName=ROLE_NAMES[key], PolicyName=_ROLE_POLICY_NAMES[key]
        ),
        "live operator inline policy",
    )
    if (
        _document(response.get("PolicyDocument"), "live inline document")
        != expected_policy
    ):
        raise ValueError("live operator inline policy differs from the reviewed plan")


def _simulate(
    iam: _IamClient,
    role_arn: str,
    action: str,
    resource: str,
    expected: str,
    context: tuple[tuple[str, str], ...] = (),
) -> None:
    arguments: dict[str, object] = {
        "PolicySourceArn": role_arn,
        "ActionNames": [action],
        "ResourceArns": [resource],
        "MaxItems": 2,
    }
    if context:
        arguments["ContextEntries"] = [
            {
                "ContextKeyName": key,
                "ContextKeyValues": [value],
                "ContextKeyType": "string",
            }
            for key, value in context
        ]
    response = mapping(
        iam.simulate_principal_policy(**arguments), "live IAM simulation"
    )
    evaluations = sequence(response.get("EvaluationResults"), "live IAM evaluations")
    if response.get("IsTruncated") or len(evaluations) != 1:
        raise ValueError("live IAM simulator response is ambiguous")
    if mapping(evaluations[0], "live IAM evaluation").get("EvalDecision") != expected:
        raise ValueError("live IAM permissions differ from the reviewed result")


def _verify_decisions(iam: _IamClient, roles: dict[str, str]) -> None:
    user = f"arn:aws:iam::{ACCOUNT}:user/minos-health-watcher"
    _simulate(
        iam,
        roles["production_operator"],
        "iam:CreateUser",
        user,
        "allowed",
        (
            (
                "iam:PermissionsBoundary",
                f"arn:aws:iam::{ACCOUNT}:policy/minos-health-watcher-boundary",
            ),
            ("aws:RequestTag/Project", "minos-health"),
        ),
    )
    _simulate(
        iam, roles["production_operator"], "iam:CreateAccessKey", user, "implicitDeny"
    )
    _simulate(
        iam,
        roles["production_operator"],
        "iam:PassRole",
        roles["state_recovery"],
        "implicitDeny",
    )
    _simulate(
        iam, roles["acceptance_operator"], "iam:CreateAccessKey", user, "implicitDeny"
    )
    state_arn = f"arn:aws:s3:::{_STATE_BUCKET}/{_STATE_KEY}"
    _simulate(
        iam, roles["acceptance_operator"], "s3:GetObject", state_arn, "implicitDeny"
    )
    _simulate(
        iam,
        roles["state_recovery"],
        "s3:DeleteObjectVersion",
        state_arn,
        "implicitDeny",
    )
    _simulate(iam, roles["state_recovery"], "s3:GetObjectVersion", state_arn, "allowed")


def _verify_recovery_read(session: _Session, sts: _StsClient, role_arn: str) -> None:
    assumed = mapping(
        sts.assume_role(
            RoleArn=role_arn,
            RoleSessionName="minos-state-recovery-check",
            DurationSeconds=900,
        ),
        "assumed recovery role",
    )
    credentials = mapping(assumed.get("Credentials"), "temporary recovery credentials")
    required = ("AccessKeyId", "SecretAccessKey", "SessionToken")
    if not all(isinstance(credentials.get(name), str) for name in required):
        raise ValueError("temporary recovery credentials are incomplete")
    s3 = cast(
        _S3Client,
        session.client(
            "s3",
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"],
        ),
    )
    result = mapping(
        s3.head_object(
            Bucket=_STATE_BUCKET, Key=_STATE_KEY, ExpectedBucketOwner=ACCOUNT
        ),
        "recovery state head",
    )
    if not isinstance(result.get("VersionId"), str):
        raise ValueError("recovery cannot read the versioned operator state")


def verify(plan_path: Path, session: _Session) -> None:
    if not plan_path.is_file() or plan_path.stat().st_size > 2_000_000:
        raise ValueError("saved operator plan JSON is unavailable")
    plan = mapping(cast(object, json.loads(plan_path.read_text())), "operator plan")
    validate_plan(plan)
    changes = {
        item["address"]: mapping(
            mapping(item["change"], "planned change")["after"], "planned values"
        )
        for raw in sequence(plan.get("resource_changes"), "plan changes")
        if isinstance(raw, dict)
        for item in [cast(dict[str, object], raw)]
        if item.get("mode") == "managed" and isinstance(item.get("address"), str)
    }
    sts = cast(_StsClient, session.client("sts"))
    if sts.get_caller_identity().get("Account") != ACCOUNT:
        raise ValueError("live verification caller belongs to another AWS account")
    iam = cast(
        _IamClient,
        session.client(
            "iam",
            config=Config(
                connect_timeout=5, read_timeout=20, retries={"max_attempts": 2}
            ),
        ),
    )
    expected_trust = _document(
        changes["aws_iam_role.production_operator"]["assume_role_policy"],
        "planned trust",
    )
    policies = {
        key: _document(changes[f"aws_iam_policy.{key}"]["policy"], "planned boundary")
        for key in POLICY_NAMES
    }
    for _attempt in range(5):
        try:
            roles = {key: verify_role(iam, key, expected_trust) for key in ROLE_NAMES}
            for key, expected in policies.items():
                verify_policy(iam, key, expected)
            for key in ROLE_NAMES:
                verify_inline_policy(iam, key, policies[key + "_boundary"])
            _verify_decisions(iam, roles)
            _verify_recovery_read(session, sts, roles["state_recovery"])
            return
        except ClientError:
            time.sleep(2)
    raise ValueError("IAM bootstrap did not pass bounded live verification")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: aws_watcher_operator_live_guard.py PLAN_JSON", file=sys.stderr)
        return 2
    try:
        session = cast(
            _Session,
            boto3.Session(profile_name="minos-health-bootstrap", region_name=_REGION),
        )
        verify(Path(sys.argv[1]), session)
    except (OSError, KeyError, ValueError, BotoCoreError, ClientError) as error:
        print(
            f"Live operator verification failed: {type(error).__name__}",
            file=sys.stderr,
        )
        return 1
    print(
        "Verified three bounded IAM roles, six boundary policies, seven deny/allow decisions, and versioned state recovery access."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
