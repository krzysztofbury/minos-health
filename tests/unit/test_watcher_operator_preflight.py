# pyright: reportMissingTypeStubs=false

from collections.abc import Mapping
from typing import final

import pytest
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from scripts.aws_watcher_operator_preflight import validate_absence


def _missing(operation: str, code: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": "bounded fixture"}}, operation
    )


@final
class _Iam:
    def __init__(
        self, *, existing_role: bool = False, denied_policy: bool = False
    ) -> None:
        self.existing_role = existing_role
        self.denied_policy = denied_policy
        self.roles: list[object] = []
        self.boundaries: list[object] = []

    def get_role(self, **kwargs: object) -> Mapping[str, object]:
        self.roles.append(kwargs.get("RoleName"))
        if self.existing_role:
            return {"Role": {"RoleName": kwargs.get("RoleName")}}
        raise _missing("GetRole", "NoSuchEntity")

    def get_policy(self, **kwargs: object) -> Mapping[str, object]:
        self.boundaries.append(kwargs.get("PolicyArn"))
        raise _missing(
            "GetPolicy", "AccessDenied" if self.denied_policy else "NoSuchEntity"
        )


@final
class _S3:
    def __init__(self, *, state_exists: bool = False) -> None:
        self.state_exists = state_exists
        self.requests: list[dict[str, object]] = []

    def head_object(self, **kwargs: object) -> Mapping[str, object]:
        self.requests.append(kwargs)
        if self.state_exists:
            return {"ContentLength": 1}
        raise _missing("HeadObject", "404")


def test_absence_check_is_exact_and_bounded() -> None:
    iam = _Iam()
    s3 = _S3()

    validate_absence(iam, s3)

    assert len(iam.roles) == 3
    assert len(iam.boundaries) == 6
    assert s3.requests == [
        {
            "Bucket": "minos-health-tfstate-218375352108-eu-central-1",
            "Key": "watcher-operators/terraform.tfstate",
            "ExpectedBucketOwner": "218375352108",
        }
    ]


@pytest.mark.parametrize(
    ("iam", "s3"),
    [
        (_Iam(existing_role=True), _S3()),
        (_Iam(denied_policy=True), _S3()),
        (_Iam(), _S3(state_exists=True)),
    ],
)
def test_existing_or_unreadable_targets_stop_before_apply(iam: _Iam, s3: _S3) -> None:
    with pytest.raises(RuntimeError):
        validate_absence(iam, s3)
