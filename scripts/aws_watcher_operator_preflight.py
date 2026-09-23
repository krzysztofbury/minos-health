# pyright: reportMissingTypeStubs=false

"""Reject a first IAM bootstrap when any planned target exists outside state."""

import sys
from collections.abc import Mapping
from typing import Protocol, cast

import boto3  # type: ignore[import-untyped]
from botocore.exceptions import (  # type: ignore[import-untyped]
    BotoCoreError,
    ClientError,
)

from scripts.aws_watcher_operator_plan_guard import (
    ACCOUNT,
    POLICY_NAMES,
    ROLE_NAMES,
    mapping,
)

_BUCKET = f"minos-health-tfstate-{ACCOUNT}-eu-central-1"
_STATE_KEY = "watcher-operators/terraform.tfstate"


class _Iam(Protocol):
    def get_role(self, **kwargs: object) -> Mapping[str, object]: ...

    def get_policy(self, **kwargs: object) -> Mapping[str, object]: ...


class _S3(Protocol):
    def head_object(self, **kwargs: object) -> Mapping[str, object]: ...


class _Sts(Protocol):
    def get_caller_identity(self) -> Mapping[str, object]: ...


class _Session(Protocol):
    def client(self, service_name: str) -> object: ...


class _ClientErrorLike(Protocol):
    response: Mapping[str, object]


def _error_code(error: ClientError) -> str | None:
    response = cast(_ClientErrorLike, cast(object, error)).response
    code = mapping(response.get("Error"), "AWS error").get("Code")
    return code if isinstance(code, str) else None


def validate_absence(iam: _Iam, s3: _S3) -> None:
    for name in ROLE_NAMES.values():
        try:
            _ = iam.get_role(RoleName=name)
        except ClientError as error:
            if _error_code(error) == "NoSuchEntity":
                continue
            raise RuntimeError("IAM role lookup failed") from None
        raise RuntimeError("a planned IAM role already exists outside Terraform state")
    for name in POLICY_NAMES.values():
        arn = f"arn:aws:iam::{ACCOUNT}:policy/{name}"
        try:
            _ = iam.get_policy(PolicyArn=arn)
        except ClientError as error:
            if _error_code(error) == "NoSuchEntity":
                continue
            raise RuntimeError("IAM boundary lookup failed") from None
        raise RuntimeError(
            "a planned IAM boundary already exists outside Terraform state"
        )
    try:
        _ = s3.head_object(Bucket=_BUCKET, Key=_STATE_KEY, ExpectedBucketOwner=ACCOUNT)
    except ClientError as error:
        if _error_code(error) in ("404", "NoSuchKey"):
            return
        raise RuntimeError("Terraform state lookup failed") from None
    raise RuntimeError("operator state was created after the saved plan")


def main() -> int:
    if len(sys.argv) != 1:
        print("usage: aws_watcher_operator_preflight.py", file=sys.stderr)
        return 2
    try:
        session = cast(
            _Session,
            boto3.Session(
                profile_name="minos-health-bootstrap", region_name="eu-central-1"
            ),
        )
        identity = cast(_Sts, session.client("sts")).get_caller_identity()
        if identity.get("Account") != ACCOUNT:
            raise RuntimeError("operator preflight belongs to another account")
        validate_absence(
            cast(_Iam, session.client("iam")), cast(_S3, session.client("s3"))
        )
    except BotoCoreError, ClientError, OSError, RuntimeError:
        print("IAM bootstrap target preflight failed", file=sys.stderr)
        return 1
    print("Verified absence of three roles, six boundaries and the operator state key.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
